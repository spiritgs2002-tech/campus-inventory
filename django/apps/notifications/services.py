from django.db import transaction
from apps.catalog.models import Item
from apps.inventory import orders
from apps.core import services as core
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from .models import Notification


def create_notification(
    tenant_id,
    recipient_user_id,
    recipient_name,
    type,
    title,
    body,
    ref_type=None,
    ref_id=None,
):
    require_runtime()
    return Notification.objects.create(
        tenant_id=tenant_id,
        recipient_user_id=recipient_user_id,
        recipient_name=recipient_name,
        type=type,
        title=title,
        body=body,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def notify_arrival(req):
    item = Item.objects.get(tenant_id=req.tenant_id, pk=req.item_id)
    return create_notification(
        req.tenant_id,
        req.applicant_id,
        req.applicant_name,
        "requisition_arrived",
        f"您申请的{item.name}已到货",
        "请前往总务处领取",
        "requisition",
        req.pk,
    )


def notify_rejected(req):
    return create_notification(
        req.tenant_id,
        req.applicant_id,
        req.applicant_name,
        "requisition_rejected",
        "您的请购申请被驳回",
        req.reject_reason,
        "requisition",
        req.pk,
    )


def owned(principal):
    require_runtime()
    p = orders.identity(principal)
    return Notification.objects.filter(
        tenant_id=p.tenant_id, recipient_user_id=p.user_id
    )


@transaction.atomic
def mark_read(principal, pk):
    require_runtime()
    p = orders.identity(principal)
    core.lock_order_context(p, lambda: [])
    row = owned(p).select_for_update().filter(pk=pk).first()
    if row is None:
        raise BusinessError("FORBIDDEN", "无权访问该通知", 403)
    row.is_read = True
    row.save(update_fields=["is_read"])
    return {"id": row.pk, "is_read": True}
