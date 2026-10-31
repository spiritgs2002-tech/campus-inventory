"""Bounded server-side validation. Original evidence bytes are never rewritten."""

from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path
import warnings
from django.conf import settings
from PIL import Image
from pypdf import PdfReader, apply_configuration
from shared.errors import BusinessError

MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
}


@dataclass(frozen=True)
class Evidence:
    data: bytes
    sha256: str
    content_type: str
    extension: str


def passive_pdf(data):
    with apply_configuration(maximum_declared_stream_length=20 * 1024 * 1024):
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 200:
            raise ValueError("encrypted or oversized PDF")
        pending, visited = [reader.trailer["/Root"]], set()
        forbidden = {
            "/JS",
            "/JavaScript",
            "/AA",
            "/OpenAction",
            "/EmbeddedFiles",
            "/RichMediaContent",
        }
        while pending:
            obj = pending.pop()
            if hasattr(obj, "get_object"):
                obj = obj.get_object()
            if id(obj) in visited:
                continue
            visited.add(id(obj))
            if len(visited) > 10000:
                raise ValueError("PDF structure too complex")
            if isinstance(obj, dict):
                if forbidden.intersection(obj) or obj.get("/S") in {
                    "/Launch",
                    "/SubmitForm",
                    "/ImportData",
                    "/GoToR",
                    "/URI",
                }:
                    raise ValueError("active PDF content")
                pending.extend(obj.values())
            elif isinstance(obj, (tuple, list)):
                pending.extend(obj)


def inspect(file, expected_sha256=""):
    extension = Path(file.name).suffix.lower()
    if extension not in MIME:
        raise BusinessError("UNSUPPORTED_FILE", "仅支持 PNG、JPEG 和静态 PDF", 422)
    data = file.read(settings.ATTACHMENT_MAX_BYTES + 1)
    if not data or len(data) > settings.ATTACHMENT_MAX_BYTES:
        raise BusinessError("INVALID_FILE_SIZE", "附件须为1字节至10MiB", 422)
    content_type = MIME[extension]
    if file.content_type not in {content_type, "application/octet-stream"}:
        raise BusinessError("CONTENT_TYPE_MISMATCH", "文件扩展名与声明类型不匹配", 422)
    try:
        if extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError("invalid PDF signature")
            passive_pdf(data)
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                    if (
                        MIME[extension] != Image.MIME[image.format]
                        or image.width * image.height > 20_000_000
                    ):
                        raise ValueError("image type or dimensions invalid")
                    image.verify()
                with Image.open(BytesIO(data), formats=["PNG", "JPEG"]) as image:
                    image.load()
    except Exception:
        raise BusinessError(
            "INVALID_FILE_CONTENT", "文件损坏、类型不符或包含不支持的活动内容", 422
        ) from None
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and expected_sha256 != digest:
        raise BusinessError("DIGEST_MISMATCH", "文件摘要与声明不符", 422)
    return Evidence(
        data, digest, content_type, ".jpg" if extension == ".jpeg" else extension
    )
