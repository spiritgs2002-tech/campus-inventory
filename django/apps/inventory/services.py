from .models import AssetCard, OutboundOrder, ScrapOrder, AdjustmentOrder


def user_has_open_work(tenant_id, user_id):
    return (
        AssetCard.objects.filter(
            tenant_id=tenant_id, responsible_user_id=user_id, status="in_use"
        ).exists()
        or OutboundOrder.objects.filter(
            tenant_id=tenant_id, applicant_id=user_id, status="pending"
        ).exists()
        or ScrapOrder.objects.filter(
            tenant_id=tenant_id, applicant_id=user_id, status="pending"
        ).exists()
        or AdjustmentOrder.objects.filter(
            tenant_id=tenant_id, applicant_id=user_id, status="pending"
        ).exists()
    )
