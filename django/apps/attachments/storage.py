"""No public URLs. Tenant prefix is checked before any object storage access."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone
import heapq
from urllib.parse import urlparse
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from shared.errors import BusinessError


def validate_key(tenant_id, key):
    if not isinstance(key, str) or not re.fullmatch(
        rf"tenants/{int(tenant_id)}/evidence/[a-f0-9]{{64}}\.(png|jpg|pdf)", key
    ):
        raise BusinessError("INVALID_OBJECT_SCOPE", "附件对象归属无效", 404)


def verify(data, expected_hash, expected_size):
    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_hash:
        raise BusinessError("OBJECT_INTEGRITY_ERROR", "附件完整性校验失败", 409)
    return data


class LocalStore:
    def __init__(self):
        if not settings.DEBUG or not settings.ATTACHMENT_STORAGE_ROOT:
            raise BusinessError(
                "STORAGE_NOT_CONFIGURED", "本地附件存储仅用于显式配置的开发验收", 503
            )
        self.root = Path(settings.ATTACHMENT_STORAGE_ROOT).resolve()
        for public in [settings.STATIC_ROOT, *settings.STATICFILES_DIRS]:
            if self.root.is_relative_to(Path(public).resolve()):
                raise BusinessError(
                    "UNSAFE_STORAGE_ROOT", "附件目录不能位于公开静态目录", 503
                )
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, tenant_id, key):
        validate_key(tenant_id, key)
        path = self.root / key
        current = self.root
        for part in Path(key).parts:
            current = current / part
            if current.is_symlink():
                raise BusinessError(
                    "INVALID_OBJECT_SCOPE", "附件路径不能包含符号链接", 404
                )
        if not path.resolve().is_relative_to(self.root):
            raise BusinessError("INVALID_OBJECT_SCOPE", "附件路径无效", 404)
        return path

    def put(self, tenant_id, key, evidence):
        path = self.path(tenant_id, key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            self.read(tenant_id, key, evidence.sha256, len(evidence.data))
            return
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(evidence.data)
            stream.flush()
            os.fsync(stream.fileno())

    def read(self, tenant_id, key, sha256, size):
        path = self.path(tenant_id, key)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(settings.ATTACHMENT_MAX_BYTES + 1)
        except FileNotFoundError:
            raise BusinessError("OBJECT_NOT_FOUND", "附件对象不存在", 404) from None
        return verify(data, sha256, size)

    def cleanup_ready(self):
        return None

    def stat(self, tenant_id, key):
        path = self.path(tenant_id, key)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise BusinessError("INVALID_OBJECT_SCOPE", "对象不是普通文件", 404)
        return {
            "key": key,
            "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            "etag": f'"{info.st_mtime_ns}-{info.st_size}"',
            "size": info.st_size,
        }

    def scan(self, tenant_id, cursor=None, limit=100):
        self.path(tenant_id, f"tenants/{tenant_id}/evidence/{'0' * 64}.png")
        prefix = self.root / f"tenants/{tenant_id}/evidence"

        def keys():
            if prefix.is_dir() and not prefix.is_symlink():
                for path in prefix.iterdir():
                    key = path.relative_to(self.root).as_posix()
                    if (
                        not path.is_symlink()
                        and path.is_file()
                        and (not cursor or key > cursor)
                    ):
                        try:
                            validate_key(tenant_id, key)
                        except BusinessError:
                            continue
                        yield key

        selected = heapq.nsmallest(limit + 1, keys())
        return [
            entry for key in selected[:limit] if (entry := self.stat(tenant_id, key))
        ], (selected[limit - 1] if len(selected) > limit else None)

    def delete(self, tenant_id, key, expected=None):
        current = self.stat(tenant_id, key)
        if current is None:
            return False
        if expected and (current["etag"], current["modified"]) != (
            expected["etag"],
            expected["modified"],
        ):
            raise BusinessError("OBJECT_CHANGED", "对象已变化，未删除", 409)
        try:
            self.path(tenant_id, key).unlink()
        except FileNotFoundError:
            return False
        return True


class S3Store:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    def put(self, tenant_id, key, evidence):
        validate_key(tenant_id, key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=evidence.data,
                ContentType=evidence.content_type,
                ContentLength=len(evidence.data),
                Metadata={"sha256": evidence.sha256, "tenant": str(tenant_id)},
                ServerSideEncryption="AES256",
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"PreconditionFailed", "412"}:
                raise BusinessError(
                    "STORAGE_UNAVAILABLE", "对象存储暂不可用，请使用原幂等键重试", 503
                ) from None
            self.read(tenant_id, key, evidence.sha256, len(evidence.data))
        except BotoCoreError:
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "对象存储暂不可用", 503
            ) from None

    def read(self, tenant_id, key, sha256, size):
        validate_key(tenant_id, key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            stream = response["Body"]
            try:
                data = stream.read(settings.ATTACHMENT_MAX_BYTES + 1)
            finally:
                stream.close()
        except ClientError as error:
            status = (
                404 if error.response["Error"]["Code"] in {"NoSuchKey", "404"} else 503
            )
            raise BusinessError(
                "OBJECT_NOT_FOUND" if status == 404 else "STORAGE_UNAVAILABLE",
                "附件对象不存在或存储暂不可用",
                status,
            ) from None
        except BotoCoreError:
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "对象存储暂不可用", 503
            ) from None
        return verify(data, sha256, size)

    def cleanup_ready(self):
        try:
            state = self.client.get_bucket_versioning(Bucket=self.bucket)
        except (ClientError, BotoCoreError):
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "无法核验清理存储配置", 503
            ) from None
        # A simple DELETE in a versioned bucket is only a delete marker. Do not
        # claim physical cleanup or purge historical versions without that policy.
        if state.get("Status") in {"Enabled", "Suspended"}:
            raise BusinessError(
                "VERSIONED_BUCKET_CLEANUP_UNSUPPORTED",
                "版本桶须独立确认历史版本策略，自动清理未执行",
                503,
            )

    def stat(self, tenant_id, key):
        validate_key(tenant_id, key)
        try:
            obj = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "NotFound", "404"}:
                return None
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "无法核验对象版本", 503
            ) from None
        except BotoCoreError:
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "对象存储暂不可用", 503
            ) from None
        return {
            "key": key,
            "modified": obj["LastModified"].isoformat(),
            "etag": obj["ETag"],
            "size": obj["ContentLength"],
        }

    def scan(self, tenant_id, cursor=None, limit=100):
        params = {
            "Bucket": self.bucket,
            "Prefix": f"tenants/{tenant_id}/evidence/",
            "MaxKeys": limit,
        }
        if cursor:
            params["ContinuationToken"] = cursor
        try:
            page = self.client.list_objects_v2(**params)
        except (ClientError, BotoCoreError):
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "无法列出待核验对象", 503
            ) from None
        entries = []
        for obj in page.get("Contents", []):
            try:
                validate_key(tenant_id, obj["Key"])
            except BusinessError:
                continue
            entries.append(
                {
                    "key": obj["Key"],
                    "modified": obj["LastModified"].isoformat(),
                    "etag": obj["ETag"],
                    "size": obj["Size"],
                }
            )
        return entries, page.get("NextContinuationToken") if page.get(
            "IsTruncated"
        ) else None

    def delete(self, tenant_id, key, expected=None):
        validate_key(tenant_id, key)
        if expected is None:
            expected = self.stat(tenant_id, key)
        if expected is None:
            return False
        try:
            self.client.delete_object(
                Bucket=self.bucket, Key=key, IfMatch=expected["etag"]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                return False
            if error.response["Error"]["Code"] in {"PreconditionFailed", "412"}:
                raise BusinessError(
                    "OBJECT_CHANGED", "对象已变化，未删除", 409
                ) from None
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "对象删除未确认，请重试", 503
            ) from None
        except BotoCoreError:
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "对象删除未确认，请重试", 503
            ) from None
        return True


@lru_cache(maxsize=4)
def s3_client(endpoint, region):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def backend():
    if settings.ATTACHMENT_STORAGE_BACKEND == "filesystem":
        return LocalStore()
    if settings.ATTACHMENT_STORAGE_BACKEND != "s3" or not settings.ATTACHMENT_S3_BUCKET:
        raise BusinessError("STORAGE_NOT_CONFIGURED", "请配置私有附件对象存储", 503)
    endpoint = settings.ATTACHMENT_S3_ENDPOINT
    if endpoint and (
        urlparse(endpoint).scheme != "https"
        and not (settings.DEBUG and urlparse(endpoint).scheme == "http")
    ):
        raise BusinessError("UNSAFE_STORAGE_ENDPOINT", "对象存储须使用HTTPS", 503)
    return S3Store(
        s3_client(endpoint, settings.ATTACHMENT_S3_REGION),
        settings.ATTACHMENT_S3_BUCKET,
    )
