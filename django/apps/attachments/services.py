"""Evidence registration, authorization and immutable association commands."""

import csv
from datetime import timedelta
import hashlib
import hmac
from io import StringIO
from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from apps.core import services as core
from apps.inventory import orders
from apps.inventory.models import ScrapOrder
from shared.errors import BusinessError
from shared.idempotency.service import execute, validate_key
from . import content, storage
from .models import Attachment, ScrapAttachment


def visible(principal):
    rows = Attachment.objects.filter(
        tenant_id=principal.tenant_id, deleted_at__isnull=True
    )
    if principal.role != "admin":
        associations = ScrapAttachment.objects.filter(
            tenant_id=principal.tenant_id,
            attachment_id=OuterRef("pk"),
            scrap__applicant_id=principal.user_id,
        )
        rows = rows.annotate(on_own_application=Exists(associations)).filter(
            Q(uploaded_by=principal.user_id) | Q(on_own_application=True)
        )
    return rows


def record(obj):
    return {
        name: (
            getattr(obj, name).isoformat()
            if hasattr(getattr(obj, name), "isoformat")
            else getattr(obj, name)
        )
        for name in (
            "id",
            "sha256",
            "size_bytes",
            "content_type",
            "retention_until",
            "legal_hold",
            "uploaded_by",
            "created_at",
        )
    }


def get(principal, pk):
    obj = visible(principal).filter(pk=pk).first()
    if obj is None:
        raise BusinessError("NOT_FOUND", "附件不存在或无权访问", 404)
    storage.validate_key(principal.tenant_id, obj.object_key)
    return obj


def command(principal, operation, key, payload, callback, discover=lambda: []):
    p = orders.identity(principal)

    def work():
        tenant, users, current = core.lock_order_context(p, discover)
        return callback(current)

    try:
        return execute(p.tenant_id, p.user_id, operation, key, payload, work)
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) in {
            "23503",
            "23505",
            "23514",
            "P0001",
        }:
            raise BusinessError(
                "EVIDENCE_CONFLICT", "附件或单据状态冲突，操作已回滚", 409
            ) from None
        raise


def upload(principal, key, file, expected_sha256=""):
    p = orders.identity(principal)
    validate_key(key)
    try:
        days = int(settings.ATTACHMENT_RETENTION_DAYS)
        if not 1 <= days <= 36525:
            raise ValueError()
    except (ValueError, TypeError):
        raise BusinessError(
            "RETENTION_NOT_CONFIGURED", "运维须先配置学校确认的附件保留期", 503
        ) from None
    evidence = content.inspect(file, expected_sha256)
    payload = {
        "sha256": evidence.sha256,
        "size": len(evidence.data),
        "content_type": evidence.content_type,
    }
    identity = (
        f"{p.tenant_id}:{p.user_id}:{key}:{evidence.sha256}:{evidence.content_type}"
    )
    object_id = hmac.new(
        settings.AUTH_SIGNING_KEY.encode(), identity.encode(), hashlib.sha256
    ).hexdigest()
    object_key = f"tenants/{p.tenant_id}/evidence/{object_id}{evidence.extension}"

    def save(current):
        store = storage.backend()
        store.put(current.tenant_id, object_key, evidence)
        # Registration trusts neither browser metadata nor the store's ETag.
        store.read(current.tenant_id, object_key, evidence.sha256, len(evidence.data))
        obj = Attachment.objects.create(
            tenant_id=current.tenant_id,
            object_key=object_key,
            sha256=evidence.sha256,
            size_bytes=len(evidence.data),
            content_type=evidence.content_type,
            retention_until=timezone.now() + timedelta(days=days),
            uploaded_by=current.user_id,
        )
        core.audit(
            current,
            "attachment",
            obj.pk,
            None,
            {"sha256": obj.sha256, "size_bytes": obj.size_bytes},
            "上传证据",
        )
        return {
            "record": record(obj),
            "business_refs": [{"type": "attachment", "id": obj.pk}],
        }

    return command(p, "attachments.upload", key, payload, save)


def download(principal, pk):
    p = orders.identity(principal)
    obj = get(p, pk)
    data = storage.backend().read(
        p.tenant_id, obj.object_key, obj.sha256, obj.size_bytes
    )
    return obj, data


def for_scrap(principal, pk):
    orders.header_get(principal, "scrap", pk)
    ids = ScrapAttachment.objects.filter(
        tenant_id=principal.tenant_id, scrap_id=pk
    ).values("attachment_id")
    return [record(obj) for obj in visible(principal).filter(pk__in=ids).order_by("id")]


def _associations(p, pairs, *, withdraw=False, reason=""):
    order_ids = {pair["scrap_id"] for pair in pairs}
    attachment_ids = {pair["attachment_id"] for pair in pairs}
    headers = ScrapOrder.objects.filter(tenant_id=p.tenant_id, pk__in=order_ids)
    if p.role != "admin":
        headers = headers.filter(applicant_id=p.user_id)
    headers = list(headers.order_by("id").select_for_update(no_key=True))
    if len(headers) != len(order_ids):
        raise BusinessError("NOT_FOUND", "处置单不存在或无权处理", 404)
    if any(h.status != "pending" or h.decided_by is not None for h in headers):
        raise BusinessError("EVIDENCE_FROZEN", "单据已决策，证据不能新增或撤回", 409)
    attachments = Attachment.objects.filter(
        tenant_id=p.tenant_id, pk__in=attachment_ids, deleted_at__isnull=True
    )
    if p.role != "admin":
        attachments = attachments.filter(uploaded_by=p.user_id)
    objects = list(attachments.order_by("id").select_for_update(no_key=True))
    if len(objects) != len(attachment_ids):
        raise BusinessError("NOT_FOUND", "附件不存在或不属于当前操作者", 404)
    for obj in objects:
        storage.validate_key(p.tenant_id, obj.object_key)
    for pair in pairs:
        query = ScrapAttachment.objects.filter(tenant_id=p.tenant_id, **pair)
        if withdraw:
            if not query.exists():
                raise BusinessError("NOT_FOUND", "证据关联不存在", 404)
            query.delete()
        else:
            if query.exists():
                raise BusinessError("ALREADY_LINKED", "证据已关联", 409)
            ScrapAttachment.objects.create(tenant_id=p.tenant_id, **pair)
        core.audit(
            p,
            "scrap_evidence",
            pair["scrap_id"],
            {"attachment_id": pair["attachment_id"]} if withdraw else None,
            {
                "action": "withdraw" if withdraw else "link",
                "attachment_id": pair["attachment_id"],
            },
            reason,
        )
    return {
        "associations": pairs,
        "withdrawn": withdraw,
        "business_refs": [{"type": "scrap", "id": pk} for pk in sorted(order_ids)]
        + [{"type": "attachment", "id": pk} for pk in sorted(attachment_ids)],
    }


def associate(principal, key, scrap_id, attachment_id, *, withdraw=False, reason=""):
    if not isinstance(reason, str) or len(reason) > 2000:
        raise BusinessError("INVALID_REASON", "说明无效", 422)
    if withdraw and not reason.strip():
        raise BusinessError("REASON_REQUIRED", "撤回证据须填写原因", 422)
    pair = {"scrap_id": scrap_id, "attachment_id": attachment_id}
    return command(
        principal,
        f"attachments.{'withdraw' if withdraw else 'link'}.{scrap_id}.{attachment_id}",
        key,
        {**pair, "reason": reason},
        lambda current: _associations(
            current, [pair], withdraw=withdraw, reason=reason
        ),
        lambda: ScrapOrder.objects.filter(
            tenant_id=principal.tenant_id, pk=scrap_id
        ).values_list("applicant_id", flat=True),
    )


def legal_hold(principal, key, pk, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise BusinessError("REASON_REQUIRED", "保全须填写原因", 422)

    def hold(current):
        if current.role != "admin":
            raise BusinessError("FORBIDDEN", "仅管理员可设置保全", 403)
        obj = visible(current).filter(pk=pk).select_for_update(no_key=True).first()
        if obj is None:
            raise BusinessError("NOT_FOUND", "附件不存在", 404)
        before = obj.legal_hold
        obj.legal_hold = True
        obj.save(update_fields=["legal_hold"])
        core.audit(
            current,
            "attachment",
            pk,
            {"legal_hold": before},
            {"legal_hold": True},
            reason,
        )
        return {
            "record": record(obj),
            "business_refs": [{"type": "attachment", "id": pk}],
        }

    return command(principal, f"attachments.hold.{pk}", key, {"reason": reason}, hold)


def raw_metadata(principal, ids):
    p = orders.identity(principal)
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= 200
        or any(type(pk) is not int or not 0 < pk < 2**63 for pk in ids)
    ):
        raise BusinessError("INVALID_IDS", "附件ID列表无效", 422)
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT a.id,a.sha256,a.size_bytes FROM attachments a
            WHERE a.tenant_id=%s AND a.id=ANY(%s) AND a.deleted_at IS NULL AND
            (%s OR a.uploaded_by=%s OR EXISTS(SELECT 1 FROM scrap_attachments sa
                JOIN scrap_orders s ON s.tenant_id=sa.tenant_id AND s.id=sa.scrap_id
                WHERE sa.tenant_id=a.tenant_id AND sa.attachment_id=a.id AND s.applicant_id=%s)) ORDER BY a.id""",
            [p.tenant_id, ids, p.role == "admin", p.user_id, p.user_id],
        )
        return [
            {"id": row[0], "sha256": row[1], "size_bytes": row[2]}
            for row in cursor.fetchall()
        ]


def import_csv(principal, key, data):
    if len(data) > 65536:
        raise BusinessError("INVALID_IMPORT", "关联清单最多64KiB、200行", 422)
    try:
        reader = csv.DictReader(StringIO(data.decode("utf-8-sig")), strict=True)
        if (
            reader.fieldnames is None
            or set(reader.fieldnames) != {"scrap_id", "attachment_id"}
            or len(reader.fieldnames) != 2
        ):
            raise ValueError()
        pairs = []
        for row in reader:
            if set(row) != {"scrap_id", "attachment_id"}:
                raise ValueError()
            pair = {name: int(value) for name, value in row.items()}
            if len(pairs) >= 200 or any(
                not 0 < value < 2**63 for value in pair.values()
            ):
                raise ValueError()
            pairs.append(pair)
        if not pairs or len({tuple(sorted(pair.items())) for pair in pairs}) != len(
            pairs
        ):
            raise ValueError()
    except (UnicodeError, csv.Error, ValueError, TypeError):
        raise BusinessError(
            "INVALID_IMPORT",
            "CSV须只含scrap_id、attachment_id，且为1至200条不重复记录",
            422,
        ) from None
    return command(
        principal,
        "attachments.import",
        key,
        {"pairs": pairs},
        lambda current: _associations(current, pairs, reason="批量导入证据关联"),
        lambda: ScrapOrder.objects.filter(
            tenant_id=principal.tenant_id, pk__in={r["scrap_id"] for r in pairs}
        ).values_list("applicant_id", flat=True),
    )
