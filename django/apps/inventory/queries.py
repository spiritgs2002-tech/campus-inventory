"""Tenant-scoped read models. Ordinary users see availability and their assets."""

from decimal import Decimal
import re
from django import forms
from django.core.paginator import Paginator
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from apps.catalog.models import Item
from shared.errors import BusinessError
from . import orders
from .models import AssetCard, AssetEvent, Inventory, StockLedger

STATUSES = {
    "in_stock": "在库",
    "in_use": "在用",
    "repair": "维修中",
    "scrapped": "已报废",
    "returned_to_supplier": "已退供应商",
    "lost": "遗失",
}
ACTIONS = {
    "opening": "期初建卡",
    "purchase": "采购建卡",
    "issue": "领用发放",
    "return": "归还",
    "repair_start": "开始维修",
    "repair_finish": "维修完成",
    "scrap": "报废",
    "purchase_return": "采购退货",
    "loss": "遗失",
    "found": "遗失找回",
    "found_gain": "盘盈建卡",
}
QUALITY = {"available": "可用", "damaged": "损坏"}


class ReadFilter(forms.Form):
    q = forms.CharField(max_length=100, required=False, label="名称 / 编码")
    item_id = forms.IntegerField(
        label="物资", min_value=1, max_value=2**63 - 1, required=False
    )
    location_id = forms.IntegerField(
        label="库位", min_value=1, max_value=2**63 - 1, required=False
    )
    recipient_id = forms.IntegerField(
        label="责任人", min_value=1, max_value=2**63 - 1, required=False
    )
    quality = forms.ChoiceField(
        label="质量", choices=[("", "全部质量"), *QUALITY.items()], required=False
    )
    status = forms.ChoiceField(
        label="状态", choices=[("", "全部状态"), *STATUSES.items()], required=False
    )
    direction = forms.ChoiceField(
        label="方向",
        choices=[("", "全部方向"), ("in", "增加"), ("out", "减少")],
        required=False,
    )
    low = forms.ChoiceField(
        label="库存提示",
        choices=[("", "全部"), ("true", "不足安全库存")],
        required=False,
    )
    page = forms.IntegerField(min_value=1, required=False, initial=1)


def filters(params, fields):
    form = ReadFilter(data=params)
    form.fields = {
        name: field for name, field in form.fields.items() if name in fields | {"page"}
    }
    return form, orders.validate(form, params)


def page(rows, number, serialize):
    current = Paginator(rows, 25).get_page(number or 1)
    return {
        "records": [serialize(row) for row in current],
        "page": current.number,
        "pages": current.paginator.num_pages,
        "total": current.paginator.count,
    }


def available_total(tenant_id, item_field):
    totals = (
        Inventory.objects.filter(
            tenant_id=tenant_id, item_id=OuterRef(item_field), quality="available"
        )
        .values("item_id")
        .annotate(total=Sum("quantity"))
        .values("total")
    )
    return Coalesce(
        Subquery(totals, output_field=DecimalField()),
        Value(Decimal(0)),
        output_field=DecimalField(),
    )


def shortage(amount, minimum):
    return amount <= 0 or amount < (minimum or Decimal(0))


def stock(principal, params):
    p = orders.identity(principal)
    allowed = {"q", "item_id", "low"}
    if p.role == "admin":
        allowed |= {"location_id", "quality"}
    form, data = filters(params, allowed)
    if p.role == "admin":
        rows = (
            Inventory.objects.filter(tenant_id=p.tenant_id)
            .select_related("item", "location")
            .annotate(available_quantity=available_total(p.tenant_id, "item_id"))
        )
        if data.get("q"):
            rows = rows.filter(
                Q(item__name__icontains=data["q"]) | Q(item__code__icontains=data["q"])
            )
        for key in ("item_id", "location_id", "quality"):
            if data.get(key):
                rows = rows.filter(**{key: data[key]})
        if data.get("low"):
            rows = rows.filter(
                Q(available_quantity__lte=0)
                | Q(available_quantity__lt=F("item__safety_stock"))
            )

        def serialize(row):
            return {
                "id": row.pk,
                "item_id": row.item_id,
                "item_name": row.item.name,
                "code": row.item.code,
                "unit": row.item.unit,
                "location_id": row.location_id,
                "location_name": row.location.name,
                "quality": row.quality,
                "quality_label": QUALITY[row.quality],
                "quantity": str(row.quantity),
                "version": row.version,
                "updated_at": row.updated_at.isoformat(),
                "available": row.available_quantity > 0,
                "low_stock": shortage(row.available_quantity, row.item.safety_stock),
            }

        rows = rows.order_by("item_id", "location_id", "quality", "id")
    else:
        rows = Item.objects.filter(
            tenant_id=p.tenant_id, type="consumable", active=True
        ).annotate(available_quantity=available_total(p.tenant_id, "pk"))
        if data.get("q"):
            rows = rows.filter(
                Q(name__icontains=data["q"]) | Q(code__icontains=data["q"])
            )
        if data.get("item_id"):
            rows = rows.filter(pk=data["item_id"])
        if data.get("low"):
            rows = rows.filter(
                Q(available_quantity__lte=0)
                | Q(available_quantity__lt=F("safety_stock"))
            )

        def serialize(row):
            return {
                "item_id": row.pk,
                "item_name": row.name,
                "code": row.code,
                "unit": row.unit,
                "available": row.available_quantity > 0,
                "low_stock": shortage(row.available_quantity, row.safety_stock),
            }

        rows = rows.order_by("id")
    return page(rows, data["page"], serialize), form


def ledger(principal, params):
    p = orders.identity(principal)
    if p.role != "admin":
        raise BusinessError("FORBIDDEN", "完整流水仅限管理员", 403)
    form, data = filters(
        params, {"q", "item_id", "location_id", "quality", "direction"}
    )
    rows = StockLedger.objects.filter(tenant_id=p.tenant_id).select_related(
        "item", "location", "operator"
    )
    for key in ("item_id", "location_id", "quality", "direction"):
        if data.get(key):
            rows = rows.filter(**{key: data[key]})
    if data.get("q"):
        rows = rows.filter(
            Q(item__name__icontains=data["q"]) | Q(item__code__icontains=data["q"])
        )

    def serialize(row):
        return {
            **orders.record(row),
            "item_name": row.item.name,
            "location_name": row.location.name,
            "operator_name": row.operator.name,
            "quality_label": QUALITY[row.quality],
        }

    return page(rows.order_by("-id"), data["page"], serialize), form


def asset_queryset(principal):
    rows = AssetCard.objects.filter(tenant_id=principal.tenant_id).select_related(
        "item", "location", "responsible_user"
    )
    if principal.role != "admin":
        rows = rows.filter(status="in_use", responsible_user_id=principal.user_id)
    return rows


def asset_record(p, row, *, detail=False):
    result = {
        "id": row.pk,
        "asset_no": row.asset_no,
        "item_id": row.item_id,
        "item_name": row.item.name,
        "status": row.status,
        "status_label": STATUSES[row.status],
        "location_id": row.location_id,
        "location_name": row.location.name,
    }
    if p.role == "admin":
        result.update(
            responsible_user_id=row.responsible_user_id,
            responsible_name=row.responsible_user.name if row.responsible_user else "—",
            original_value=str(row.original_value)
            if row.original_value is not None
            else None,
            source_inbound_item_id=row.source_inbound_item_id,
            purchase_date=row.purchase_date.isoformat() if row.purchase_date else None,
        )
    if detail:
        result["scan_path"] = f"/assets/scan/{row.qr_token}/"
    return result


def assets(principal, params):
    p = orders.identity(principal)
    fields = {"q", "item_id", "status"}
    if p.role == "admin":
        fields |= {"location_id", "recipient_id"}
    form, data = filters(params, fields)
    rows = asset_queryset(p)
    for param, field in [
        ("item_id", "item_id"),
        ("location_id", "location_id"),
        ("status", "status"),
        ("recipient_id", "responsible_user_id"),
    ]:
        if data.get(param):
            rows = rows.filter(**{field: data[param]})
    if data.get("q"):
        rows = rows.filter(
            Q(asset_no__icontains=data["q"])
            | Q(item__name__icontains=data["q"])
            | Q(item__code__icontains=data["q"])
        )
    return page(
        rows.order_by("id"), data["page"], lambda row: asset_record(p, row)
    ), form


def asset_detail(principal, *, pk=None, token=None, params=None):
    p = orders.identity(principal)
    row = (
        asset_queryset(p)
        .filter(**({"qr_token": token} if token is not None else {"pk": pk}))
        .first()
    )
    if row is None:
        raise BusinessError("NOT_FOUND", "资产不存在或无权查看", 404)
    _, data = filters(params or {}, set())

    # Full historical responsibility and operator identities remain admin-only.
    def serialize(event):
        result = {
            name: orders.record(event)[name]
            for name in ("id", "created_at", "action", "from_status", "to_status")
        }
        result["action_label"] = ACTIONS[event.action]
        result["status_label"] = STATUSES[event.to_status]
        if p.role == "admin":
            result.update(orders.record(event))
        return result

    events = page(
        AssetEvent.objects.filter(tenant_id=p.tenant_id, asset_card_id=row.pk).order_by(
            "-id"
        ),
        data["page"],
        serialize,
    )
    return {"record": asset_record(p, row, detail=True), "events": events}


def resolve_scan(token, *, tenant_id=None):
    """One opaque label capability resolves scope; all subsequent reads use it.

    Anonymous callers have no ambient tenant. Never choose the first row when
    legacy/manual tokens collide across kinds or schools. Logged-in callers
    always provide their authenticated tenant and cannot fall back to anonymous.
    """
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise BusinessError("NOT_FOUND", "二维码无效或不属于本学校", 404)
    common = {"qr_token": token, "tenant__status": "active"}
    if tenant_id is not None:
        common["tenant_id"] = tenant_id
    live = Q(tenant__expire_at__isnull=True) | Q(
        tenant__expire_at__gte=timezone.localdate()
    )
    cards = (
        AssetCard.objects.filter(**common, item__active=True)
        .filter(live)
        .select_related("item", "location")
    )
    items = Item.objects.filter(**common, active=True, type="consumable").filter(live)
    matches = [("asset", row) for row in cards[:2]] + [
        ("item", row) for row in items[:2]
    ]
    if len(matches) != 1:
        raise BusinessError("NOT_FOUND", "二维码无效或不属于本学校", 404)
    return matches[0]
