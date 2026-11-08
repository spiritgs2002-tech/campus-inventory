from uuid import uuid4
from decimal import Decimal
from django import forms
from django.db import transaction
from apps.catalog.models import Item
from apps.core.models import Tenant
from apps.core import services as core
from apps.inventory import orders, pickup, queries
from apps.notifications.services import notify_arrival, notify_rejected
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.idempotency.service import execute, validate_key
from .models import Requisition

OPERATION = "procurement.requisition.create"


class SubmitForm(forms.Form):
    item_id = forms.IntegerField(min_value=1)
    qty = forms.DecimalField(max_digits=14, decimal_places=3, min_value=Decimal(".001"))
    reason = forms.CharField(max_length=2000)
    name = forms.CharField(max_length=120, required=False)
    employee_no = forms.RegexField(r"^[A-Za-z0-9_.-]{1,80}$", required=False)
    token = forms.CharField(max_length=128, required=False)


def record(row):
    return {
        f.name: (
            str(getattr(row, f.name))
            if f.name in ("qty", "created_at", "updated_at")
            else getattr(row, f.name)
        )
        for f in row._meta.fields
    }


def requisition_submit(principal, payload, key):
    require_runtime()
    validate_key(key)
    data = orders.validate(SubmitForm(payload), payload)
    p = orders.identity(principal) if principal is not None else None
    # Anonymous school discovery requires the existing unguessable QR capability.
    if p is None:
        kind, obj = queries.resolve_scan(data["token"])
        item_id = obj.item_id if kind == "asset" else obj.pk
        if item_id != data["item_id"]:
            raise BusinessError("NOT_FOUND", "物品与二维码不匹配", 404)
        if not data["name"] or not data["employee_no"]:
            raise BusinessError("IDENTITY_REQUIRED", "请填写姓名和工号", 422)
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update(no_key=True).get(pk=obj.tenant_id)
            pickup.tenant_available(tenant)
            pickup.anonymous_enabled(tenant.pk)
            anon = pickup.ensure_lightweight_user(
                tenant.pk, data["name"], data["employee_no"]
            )
            return _submit(anon, data, key, True)
    return _submit(p, data, key, False)


def _submit(p, data, key, anonymous):
    digest = {k: str(v) for k, v in data.items()}
    digest["qty"] = format(data["qty"].normalize(), "f")
    digest["identity"] = "unverified" if anonymous else "authenticated"

    def work():
        if anonymous:
            pickup._anonymous_context(p, lambda: [])
            pickup.anonymous_quota(p, OPERATION)
        else:
            core.lock_order_context(p, lambda: [])
        if not Item.objects.filter(
            tenant_id=p.tenant_id, pk=data["item_id"], active=True
        ).exists():
            raise BusinessError("NOT_FOUND", "物品不存在", 404)
        row = Requisition.objects.create(
            tenant_id=p.tenant_id,
            order_no="RQ-" + uuid4().hex,
            applicant_id=p.user_id,
            applicant_name=p.name,
            applicant_no=p.username,
            item_id=data["item_id"],
            qty=data["qty"],
            reason=data["reason"],
        )
        core.audit(
            p,
            "requisition",
            row.pk,
            {},
            {"action": "create", "identity": digest["identity"]},
            "请购申请",
        )
        return {
            "document": record(row),
            "business_refs": [{"type": "requisition", "id": row.pk}],
        }

    return execute(p.tenant_id, p.user_id, OPERATION, key, digest, work)


def _admin(principal):
    require_runtime()
    p = orders.identity(principal)
    if p.role != "admin":
        raise BusinessError("FORBIDDEN", "仅管理员可操作", 403)
    return p


def _locked(p, pk, status):
    core.lock_order_context(p, lambda: [])
    row = (
        Requisition.objects.select_for_update(no_key=True)
        .filter(tenant_id=p.tenant_id, pk=pk)
        .first()
    )
    if row is None:
        raise BusinessError("NOT_FOUND", "申请不存在", 404)
    if row.status != status:
        raise BusinessError("FROZEN_DOCUMENT", "申请状态不允许该操作", 409)
    return row


def decide(principal, pk, action, key, reason=""):
    p = _admin(principal)
    validate_key(key)
    if (
        action not in ("approve", "reject")
        or not isinstance(reason, str)
        or len(reason) > 2000
        or (action == "reject" and not reason.strip())
    ):
        raise BusinessError("INVALID_DECISION", "驳回须填写原因，最多2000字", 422)

    def work():
        row = _locked(p, pk, "pending")
        row.status = "approved" if action == "approve" else "rejected"
        row.approved_by = p.user_id
        row.reject_reason = reason.strip() if action == "reject" else None
        row.save(update_fields=["status", "approved_by", "reject_reason", "updated_at"])
        if action == "reject":
            notify_rejected(row)
        core.audit(p, "requisition", row.pk, {}, {"action": action}, "请购审批")
        return {
            "document": record(row),
            "business_refs": [{"type": "requisition", "id": row.pk}],
        }

    return execute(
        p.tenant_id,
        p.user_id,
        "procurement.requisition.decide",
        key,
        {"pk": pk, "action": action, "reason": reason},
        work,
    )


def fulfill(
    principal,
    pk,
    key,
    location_id,
    quality="available",
    supplier_id=None,
    unit_price=None,
):
    p = _admin(principal)
    validate_key(key)
    digest = dict(
        pk=pk,
        location_id=location_id,
        quality=quality,
        supplier_id=supplier_id,
        unit_price=unit_price,
    )

    def work():
        row = _locked(p, pk, "approved")
        payload = {
            "type": "purchase",
            "supplier_id": supplier_id,
            "remark": f"请购到货入库 · {row.order_no}",
            "lines": [
                {
                    "item_id": row.item_id,
                    "quantity": str(row.qty),
                    "location_id": location_id,
                    "quality": quality,
                    "unit_price": unit_price,
                }
            ],
        }
        result, _ = orders.create(p, "inbound", "RQ-IN-" + str(row.pk), payload)
        row.status = "purchased"
        row.inbound_order_id = result["document"]["id"]
        row.save(update_fields=["status", "inbound_order_id", "updated_at"])
        notify_arrival(row)
        core.audit(
            p,
            "requisition",
            row.pk,
            {},
            {"action": "fulfill", "inbound_order_id": row.inbound_order_id},
            "请购到货",
        )
        return {
            "document": record(row),
            "inbound": result["document"],
            "business_refs": [
                {"type": "requisition", "id": row.pk},
                *result["business_refs"],
            ],
        }

    return execute(
        p.tenant_id, p.user_id, "procurement.requisition.fulfill", key, digest, work
    )


def visible(principal):
    require_runtime()
    p = orders.identity(principal)
    rows = Requisition.objects.filter(tenant_id=p.tenant_id)
    return rows if p.role == "admin" else rows.filter(applicant_id=p.user_id)
