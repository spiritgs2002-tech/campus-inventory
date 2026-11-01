"""Capability-scoped self pickup. Never creates a general-purpose login session."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import secrets
from uuid import uuid4
from django import forms
from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from apps.catalog.models import Location
from apps.core import services as core
from apps.core.models import OperationRequest, Tenant, User
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.idempotency.service import execute, validate_key
from . import orders, posting, queries
from .models import Inventory, OutboundItem, OutboundOrder

UNVERIFIED = "!pickup-unverified:"
OPERATION = "inventory.pickup"


@dataclass(frozen=True)
class AnonymousPickup:
    tenant_id: int
    user_id: int
    username: str
    name: str
    role: str = "user"
    family_id: None = None
    must_change_password: bool = False


class PickupForm(forms.Form):
    quantity = forms.DecimalField(
        max_digits=14, decimal_places=3, min_value=Decimal(".001"), required=False
    )
    name = forms.CharField(max_length=120, required=False, strip=True)
    employee_no = forms.RegexField(
        r"^[A-Za-z0-9_.-]{1,80}$", max_length=80, required=False
    )


def validate_payload(payload, kind):
    if not isinstance(payload, dict) or set(payload) - {
        "quantity",
        "name",
        "employee_no",
    }:
        raise BusinessError(
            "INVALID_FIELDS", "仅允许数量、姓名和工号，不能指定收件人或其他物品", 422
        )
    if any(
        key in payload and not isinstance(payload[key], str)
        for key in ("name", "employee_no")
    ):
        raise BusinessError("INVALID_PICKUP", "姓名与工号须为文本", 422)
    form = PickupForm(payload)
    if not form.is_valid():
        raise BusinessError("INVALID_PICKUP", "请填写有效的数量、姓名和工号", 422)
    data = form.cleaned_data
    try:
        data["name"].encode("utf-8")
    except UnicodeError:
        raise BusinessError("INVALID_PICKUP", "姓名包含无效字符", 422) from None
    quantity = data["quantity"]
    if kind == "asset":
        if quantity is not None and quantity != 1:
            raise BusinessError(
                "INVALID_QUANTITY", "固定资产每次只能领取扫码的这一件", 422
            )
        quantity = Decimal(1)
    elif quantity is None:
        raise BusinessError("INVALID_QUANTITY", "请填写领用数量", 422)
    data["quantity"] = quantity
    return data


def ensure_lightweight_user(tenant_id, name, employee_no):
    """Caller owns the tenant lock; formal/disabled identities are never reused."""
    user = User.objects.filter(tenant_id=tenant_id, username=employee_no).first()
    if user is None:
        user = User.objects.create(
            tenant_id=tenant_id,
            username=employee_no,
            name=name,
            role="user",
            active=True,
            must_change_password=False,
            password_hash=UNVERIFIED + secrets.token_urlsafe(32),
        )
    if (
        user.role != "user"
        or not user.active
        or user.must_change_password
        or not user.password_hash.startswith(UNVERIFIED)
        or user.name != name
    ):
        raise BusinessError(
            "LOGIN_REQUIRED_FOR_EMPLOYEE",
            "该工号不能用于免密登记，请登录或联系管理员",
            409,
        )
    return AnonymousPickup(tenant_id, user.pk, user.username, user.name)


def tenant_available(tenant):
    if tenant.status != "active" or (
        tenant.expire_at and tenant.expire_at < timezone.localdate()
    ):
        raise BusinessError("NOT_FOUND", "二维码无效或学校暂不可用", 404)


def _anonymous_context(p, discover):
    tenant = Tenant.objects.select_for_update(no_key=True).get(pk=p.tenant_id)
    tenant_available(tenant)
    ids = {p.user_id, *discover()}
    users = {
        u.pk: u
        for u in User.objects.select_for_update(no_key=True)
        .filter(tenant_id=p.tenant_id, pk__in=ids)
        .order_by("id")
    }
    current = users.get(p.user_id)
    if (
        current is None
        or not current.active
        or current.role != "user"
        or current.must_change_password
        or not current.password_hash.startswith(UNVERIFIED)
        or (current.username, current.name) != (p.username, p.name)
    ):
        raise BusinessError("UNAUTHENTICATED", "免密登记身份已变化，请重新操作", 401)
    return tenant, users, p


def pickup_issue(principal, token, quantity, recipient_id, key):
    require_runtime()
    validate_key(key)
    anonymous = isinstance(principal, AnonymousPickup)
    p = principal if anonymous else orders.identity(principal)
    if p.role not in {"admin", "user"} or recipient_id != p.user_id:
        raise BusinessError("FORBIDDEN", "扫码仅限本人领取", 403)
    kind, target = queries.resolve_scan(token, tenant_id=p.tenant_id)
    quantity = validate_payload({"quantity": quantity}, kind)["quantity"]
    source_rows = [{"asset_card_id": target.pk}] if kind == "asset" else []
    digest = {
        "token": token,
        "quantity": format(quantity.normalize(), "f"),
        "recipient_id": p.user_id,
        "identity": "unverified" if anonymous else "authenticated",
    }

    def work():
        def discover():
            return posting.discover_users(p.tenant_id, source_rows, p.user_id)

        tenant, users, current = (
            _anonymous_context(p, discover)
            if anonymous
            else core.lock_order_context(p, discover)
        )
        tenant_available(tenant)
        current_kind, obj = queries.resolve_scan(token, tenant_id=current.tenant_id)
        if current_kind != kind or obj.pk != target.pk:
            raise BusinessError("NOT_FOUND", "二维码归属已变化，请重新扫码", 404)
        ctx = {
            "principal": current,
            "tenant": tenant,
            "users": users,
            "operation_id": core.operation_id(
                current.tenant_id, current.user_id, OPERATION, key
            ),
        }
        if anonymous:
            recent = OperationRequest.objects.filter(
                tenant_id=p.tenant_id,
                operation=OPERATION,
                completed_at__gte=timezone.now() - timedelta(minutes=1),
                response__pickup__identity="unverified",
            ).count()
            if recent >= settings.PICKUP_ANONYMOUS_PER_MINUTE:
                raise BusinessError(
                    "PICKUP_RATE_LIMITED",
                    "学校免密登记请求较多，请稍后重试或登录领取",
                    429,
                )
        header = OutboundOrder.objects.create(
            tenant_id=p.tenant_id,
            order_no="PK-" + uuid4().hex,
            type="requisition",
            applicant_id=current.user_id,
            status="pending",
            operation_id=ctx["operation_id"],
            remark="扫码领用 · "
            + ("姓名工号未核验登记" if anonymous else "登录本人领取"),
        )
        posting.lock_sources(ctx, source_rows)
        allocations = []
        if kind == "asset":
            card = ctx["cards"][obj.pk]
            if card.status != "in_stock" or not card.location.active:
                raise BusinessError("ASSET_UNAVAILABLE", "该资产当前不可领取", 409)
            allocations = [(card.location_id, Decimal(1), [card.pk])]
        else:
            stocks = list(
                Inventory.objects.filter(
                    tenant_id=p.tenant_id,
                    item_id=obj.pk,
                    quality="available",
                    location__active=True,
                    quantity__gt=0,
                ).order_by("location_id")
            )
            posting.lock_stock(
                ctx, [(obj.pk, s.location_id, "available") for s in stocks]
            )
            remaining = quantity
            for stock in stocks:
                available = ctx["stocks"][
                    (obj.pk, stock.location_id, "available")
                ].quantity
                take = min(remaining, available)
                if take > 0:
                    allocations.append((stock.location_id, take, []))
                    remaining -= take
                if not remaining:
                    break
            if remaining:
                raise BusinessError(
                    "INSUFFICIENT_STOCK", "当前可用库存不足，请调整数量", 409
                )
        lines, executions = [], []
        for location, amount, assets in allocations:
            lines.append(
                OutboundItem.objects.create(
                    tenant_id=p.tenant_id,
                    order_id=header.pk,
                    item_id=obj.item_id if kind == "asset" else obj.pk,
                    item_type="fixed" if kind == "asset" else "consumable",
                    requested_qty=amount,
                    location_id=location,
                    recipient_id=current.user_id,
                )
            )
            executions.append(
                {
                    "actual_qty": amount,
                    "asset_ids": assets,
                    "location_id": location,
                    "recipient_id": current.user_id,
                }
            )
        posting.issue(ctx, header, lines, executions)
        locations = dict(
            Location.objects.filter(
                tenant_id=p.tenant_id, pk__in=[row[0] for row in allocations]
            ).values_list("id", "name")
        )
        audit_changes = {
            "action": "pickup",
            "status": "completed",
            "identity": digest["identity"],
            "scan_type": kind,
        }
        if anonymous:
            audit_changes["claimed_identity"] = {
                "name": current.name,
                "employee_no": current.username,
                "verified": False,
            }
        core.audit(
            current,
            "outbound",
            header.pk,
            {},
            audit_changes,
            "扫码领用",
        )
        return {
            "document": orders.record(header),
            "business_refs": [{"type": "outbound", "id": header.pk}],
            "pickup": {
                "identity": digest["identity"],
                "quantity": digest["quantity"],
                "target_type": kind,
                "target_id": obj.pk,
                "allocations": [
                    {
                        "location_id": location,
                        "location": locations[location],
                        "quantity": format(amount.normalize(), "f"),
                    }
                    for location, amount, _ in allocations
                ],
            },
        }

    try:
        return execute(p.tenant_id, p.user_id, OPERATION, key, digest, work)
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", "") in {
            "23503",
            "23505",
            "23514",
            "P0001",
            "22003",
        }:
            raise BusinessError(
                "PICKUP_CONFLICT", "状态或库存发生冲突，请使用原幂等键重试", 409
            ) from None
        raise


def submit(principal, token, payload, key):
    require_runtime()
    validate_key(key)
    if principal is not None:
        principal = orders.identity(principal)
    kind, obj = queries.resolve_scan(
        token, tenant_id=principal.tenant_id if principal else None
    )
    data = validate_payload(payload, kind)
    if principal is not None:
        return pickup_issue(principal, token, data["quantity"], principal.user_id, key)
    if not data["name"] or not data["employee_no"]:
        raise BusinessError("IDENTITY_REQUIRED", "未登录领取须填写姓名和工号", 422)
    # User creation and the posting share one transaction. The tenant lock before
    # identity allocation prevents races with administrators creating formal users.
    with transaction.atomic():
        tenant = Tenant.objects.select_for_update(no_key=True).get(pk=obj.tenant_id)
        tenant_available(tenant)
        p = ensure_lightweight_user(obj.tenant_id, data["name"], data["employee_no"])
        return pickup_issue(p, token, data["quantity"], p.user_id, key)
