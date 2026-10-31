"""Administrator-only repair commands; every state change has an atomic event."""

from django import forms
from apps.core import services as core
from apps.catalog import services as catalog
from shared.errors import BusinessError
from shared.idempotency.service import execute
from . import orders, posting
from .models import OutboundAsset


class RepairForm(forms.Form):
    reason = forms.CharField(label="维修说明", max_length=2000, widget=forms.Textarea)
    location_id = forms.IntegerField(
        label="维修完成后库位（可选）", min_value=1, max_value=2**63 - 1, required=False
    )


def repair(principal, pk, action, key, payload):
    p = orders.identity(principal)
    if p.role != "admin":
        raise BusinessError("FORBIDDEN", "维修操作仅限管理员", 403)
    if action not in {"repair_start", "repair_finish"}:
        raise BusinessError("INVALID_ACTION", "维修操作无效", 422)
    data = orders.validate(RepairForm(data=payload), payload)
    if action == "repair_start" and data["location_id"]:
        raise BusinessError("INVALID_FIELDS", "开始维修不能改变库位", 422)
    operation = f"asset.{pk}.{action}"

    def work():
        rows = [{"asset_card_id": pk}]
        tenant, users, current = core.lock_order_context(
            p, lambda: posting.discover_users(p.tenant_id, rows)
        )
        if current.role != "admin":
            raise BusinessError("FORBIDDEN", "维修操作仅限管理员", 403)
        ctx = {
            "tenant": tenant,
            "users": users,
            "principal": current,
            "operation_id": core.operation_id(p.tenant_id, p.user_id, operation, key),
        }
        posting.lock_sources(ctx, rows)
        card = ctx["cards"][pk]
        expected, target = (
            ("in_stock", "repair")
            if action == "repair_start"
            else ("repair", "in_stock")
        )
        if card.status != expected or card.responsible_user_id is not None:
            posting.fail("INVALID_ASSET_STATE", "当前状态不能进行该维修操作")
        if OutboundAsset.objects.filter(
            tenant_id=p.tenant_id, asset_card_id=pk, closed_at__isnull=True
        ).exists():
            posting.fail("ASSET_HAS_OPEN_ISSUE", "资产仍有未结案发放关系")
        location = data["location_id"] or card.location_id
        if location != card.location_id:
            catalog.order_reference("locations", p.tenant_id, location, active=True)
        before = {"status": card.status, "location_id": card.location_id}
        posting.transition(
            ctx, card, target, location, None, action, reason=data["reason"]
        )
        after = {"status": target, "location_id": location}
        core.audit(
            current, "asset", pk, before, {"action": action, **after}, data["reason"]
        )
        return {
            "record": {"id": pk, "asset_no": card.asset_no, **after},
            "business_refs": [{"type": "asset", "id": pk}],
        }

    return execute(p.tenant_id, p.user_id, operation, key, payload, work)
