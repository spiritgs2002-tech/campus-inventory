"""Scoped repair workflow. Never changes inventory or issues login sessions."""

from uuid import uuid4
from django.db import transaction
from django.utils import timezone
from apps.attachments import content, services as attachments
from apps.attachments.models import Attachment
from apps.catalog.models import Item, Location
from apps.core import services as core
from apps.core.models import Tenant
from apps.inventory import orders, pickup, queries
from apps.inventory.models import AssetCard
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.idempotency.service import execute, validate_key
from .forms import TicketForm, DispatchForm, TransitionForm, clean_payload
from .models import RepairTicket

OPERATION = "repair.ticket.create"
MODELS = {"asset": AssetCard, "item": Item, "location": Location}
TRANSITIONS = {"start": ("dispatched", "repairing"), "done": ("repairing", "done")}


def identity(principal):
    require_runtime()
    if principal is None:
        raise BusinessError("UNAUTHENTICATED", "请先登录", 401)
    p = orders.identity(principal)
    if p.role not in {"admin", "user"}:
        raise BusinessError("FORBIDDEN", "无权访问报修工单", 403)
    return p


def record(row):
    return {
        name: (
            getattr(row, name).isoformat()
            if name in {"created_at", "updated_at"}
            else getattr(row, name)
        )
        for name in (
            "id",
            "ticket_no",
            "reporter_user_id",
            "reporter_name",
            "reporter_no",
            "target_type",
            "target_id",
            "description",
            "photo_attachment_id",
            "priority",
            "status",
            "assigned_to",
            "reject_reason",
            "created_at",
            "updated_at",
        )
    }


def anonymous_target(data):
    """An existing opaque QR label supplies school scope, never a guessed ID."""
    kind, obj = queries.resolve_scan(data["token"])
    allowed = (
        {("asset", obj.pk), ("item", obj.item_id), ("location", obj.location_id)}
        if kind == "asset"
        else {("item", obj.pk)}
    )
    allowed.add(("location", None))
    if (data["target_type"], data["target_id"]) not in allowed:
        raise BusinessError("NOT_FOUND", "报修对象与二维码不匹配", 404)
    return obj.tenant_id


def _target(tenant_id, data):
    if data["target_id"] is None:
        return
    model = MODELS[data["target_type"]]
    rows = model.objects.select_for_update(no_key=True).filter(
        tenant_id=tenant_id, pk=data["target_id"]
    )
    if model in {Item, Location}:
        rows = rows.filter(active=True)
    if not rows.exists():
        raise BusinessError("FORBIDDEN", "对象不存在、停用或不属于本学校", 403)


def _photo(p, photo_id):
    if photo_id is None:
        return
    row = (
        Attachment.objects.select_for_update(no_key=True)
        .filter(
            tenant_id=p.tenant_id,
            pk=photo_id,
            uploaded_by=p.user_id,
            deleted_at__isnull=True,
        )
        .first()
    )
    if row is None:
        raise BusinessError("FORBIDDEN", "照片不存在或不属于当前报修人", 403)
    if row.content_type not in {"image/png", "image/jpeg"} or (
        not row.legal_hold and row.retention_until <= timezone.now()
    ):
        raise BusinessError("INVALID_PHOTO", "请使用保留期内的PNG或JPEG照片", 422)


def repair_submit(principal, payload, key, *, file=None):
    require_runtime()
    validate_key(key)
    data = clean_payload(TicketForm, payload)
    data["priority"] = data["priority"] or "normal"
    p = identity(principal) if principal is not None else None
    if file is not None and data["photo_attachment_id"] is not None:
        raise BusinessError("INVALID_FIELDS", "照片上传和附件编号不可同时指定", 422)
    evidence = None
    if file is not None:
        if p is None:
            raise BusinessError("LOGIN_REQUIRED_FOR_PHOTO", "上传报修照片请先登录", 401)
        evidence = content.inspect(file)
        if evidence.content_type not in {"image/png", "image/jpeg"}:
            raise BusinessError("INVALID_PHOTO", "报修照片只接受PNG或JPEG", 422)
        file.seek(0)
    if p is not None:
        return _submit(p, data, key, file, evidence, False)
    if not data["name"] or not data["employee_no"]:
        raise BusinessError("IDENTITY_REQUIRED", "未登录报修须填写姓名和工号", 422)
    if data["photo_attachment_id"] is not None:
        raise BusinessError("LOGIN_REQUIRED_FOR_PHOTO", "附照片报修请先登录", 401)
    tenant_id = anonymous_target(data)
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update(no_key=True).get(pk=tenant_id)
        pickup.tenant_available(tenant)
        pickup.anonymous_enabled(tenant.pk)
        p = pickup.ensure_lightweight_user(tenant_id, data["name"], data["employee_no"])
        return _submit(p, data, key, None, None, True)


def _submit(p, data, key, file, evidence, anonymous):
    digest = {
        **data,
        "identity": "unverified" if anonymous else "authenticated",
        "file_sha256": evidence.sha256 if evidence else None,
    }

    def work():
        if anonymous:
            pickup._anonymous_context(p, lambda: [])
            if anonymous_target(data) != p.tenant_id:
                raise BusinessError("FORBIDDEN", "二维码学校已变化", 403)
            pickup.anonymous_quota(p, OPERATION)
        else:
            core.lock_order_context(p, lambda: [])
        _target(p.tenant_id, data)
        photo_id = data["photo_attachment_id"]
        if file is not None:
            # execute() may retry a rolled-back lock conflict using the same file.
            file.seek(0)
            result, _ = attachments.upload(
                p,
                "repair-photo-"
                + str(core.operation_id(p.tenant_id, p.user_id, OPERATION, key)),
                file,
            )
            photo_id = result["record"]["id"]
        _photo(p, photo_id)
        row = RepairTicket.objects.create(
            tenant_id=p.tenant_id,
            ticket_no="RP-" + uuid4().hex,
            reporter_user_id=p.user_id,
            reporter_name=p.name,
            reporter_no=p.username,
            target_type=data["target_type"],
            target_id=data["target_id"],
            description=data["description"],
            priority=data["priority"],
            photo_attachment_id=photo_id,
        )
        core.audit(
            p,
            "repair",
            row.pk,
            None,
            {
                "action": "create",
                "identity": digest["identity"],
                "status": row.status,
                "photo_attachment_id": photo_id,
            },
            "报修工单",
        )
        return {
            "document": record(row),
            "business_refs": [{"type": "repair", "id": row.pk}],
        }

    return execute(p.tenant_id, p.user_id, OPERATION, key, digest, work)


def repair_transition(principal, pk, action, key, *, assigned_to=None, reason=""):
    p = identity(principal)
    if p.role != "admin":
        raise BusinessError("FORBIDDEN", "仅管理员可派单及变更进度", 403)
    validate_key(key)
    if action == "dispatch":
        data = clean_payload(DispatchForm, {"assigned_to": assigned_to})
        assigned_to = data["assigned_to"]
    else:
        data = clean_payload(TransitionForm, {"action": action, "reason": reason})
        action, reason = data["action"], data["reason"]
    digest = {"pk": pk, "action": action, "assigned_to": assigned_to, "reason": reason}

    def work():
        _, users, _ = core.lock_order_context(
            p, lambda: [assigned_to] if action == "dispatch" else []
        )
        row = (
            RepairTicket.objects.select_for_update(no_key=True)
            .filter(tenant_id=p.tenant_id, pk=pk)
            .first()
        )
        if row is None:
            raise BusinessError("FORBIDDEN", "工单不存在或无权操作", 403)
        if row.status in {"done", "cancelled"}:
            raise BusinessError("FROZEN_DOCUMENT", "工单已经结束", 409)
        before = {"status": row.status, "assigned_to": row.assigned_to}
        if action == "dispatch":
            user = users.get(assigned_to)
            if user is None or not user.active:
                raise BusinessError("INVALID_ASSIGNEE", "请选择本校启用的处理人", 422)
            if row.status != "pending":
                raise BusinessError("FROZEN_DOCUMENT", "工单已派单", 409)
            row.assigned_to, row.status = assigned_to, "dispatched"
        elif action == "cancel":
            row.status, row.reject_reason = "cancelled", reason or None
        else:
            expected, target = TRANSITIONS[action]
            if row.status != expected:
                raise BusinessError("FROZEN_DOCUMENT", "不能跳过报修处理步骤", 409)
            row.status = target
        row.save(update_fields=["status", "assigned_to", "reject_reason", "updated_at"])
        core.audit(
            p,
            "repair",
            row.pk,
            before,
            {"action": action, "status": row.status, "assigned_to": row.assigned_to},
            reason or "报修处理",
        )
        return {
            "document": record(row),
            "business_refs": [{"type": "repair", "id": row.pk}],
        }

    return execute(p.tenant_id, p.user_id, "repair.ticket." + action, key, digest, work)


def visible(principal):
    p = identity(principal)
    rows = RepairTicket.objects.filter(tenant_id=p.tenant_id)
    return rows if p.role == "admin" else rows.filter(reporter_user_id=p.user_id)


def detail(principal, pk):
    row = visible(principal).filter(pk=pk).first()
    if row is None:
        raise BusinessError("FORBIDDEN", "无权查看该工单", 403)
    return row
