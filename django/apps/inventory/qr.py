from io import BytesIO
import secrets
import qrcode
from apps.catalog.models import Item
from apps.core import services as core
from shared.auth.backends import require_admin
from shared.errors import BusinessError
from shared.idempotency.service import execute
from .models import AssetCard


def qr_png(url):
    stream = BytesIO()
    qrcode.make(url).save(stream, format="PNG")
    return stream.getvalue()


def assign_tokens(tenant_id, batch_size=100, *, dry_run=False):
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise BusinessError("INVALID_BATCH_SIZE", "每批须为1至1000条", 422)
    items = list(
        Item.objects.select_for_update(no_key=True)
        .filter(
            tenant_id=tenant_id, qr_token__isnull=True, type="consumable", active=True
        )
        .order_by("id")[:batch_size]
    )
    for item in items:
        if dry_run:
            continue
        for _ in range(10):
            token = secrets.token_urlsafe(32)
            if (
                not Item.objects.filter(qr_token=token).exists()
                and not AssetCard.objects.filter(qr_token=token).exists()
            ):
                item.qr_token = token
                item.save(update_fields=["qr_token"])
                break
        else:
            raise BusinessError("QR_COLLISION", "二维码生成发生冲突，请重试", 409)
    return {
        "generated": len(items),
        "dry_run": dry_run,
        "business_refs": [{"type": "items", "id": item.pk} for item in items],
    }


def generate_item_tokens(principal, key, batch_size=100):
    principal = require_admin(principal)

    def work():
        _, _, p = core.lock_order_context(principal, lambda: set())
        if p.role != "admin":
            raise BusinessError("FORBIDDEN", "仅管理员可生成标签", 403)
        result = assign_tokens(p.tenant_id, batch_size)
        core.audit(
            p,
            "items",
            None,
            {},
            {"qr_generated": result["generated"]},
            "生成耗材二维码",
        )
        return result

    return execute(
        principal.tenant_id,
        principal.user_id,
        "catalog.item_qr",
        key,
        {"batch_size": batch_size},
        work,
    )
