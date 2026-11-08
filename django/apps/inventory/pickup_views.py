import json
from uuid import uuid4
from django import forms
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
from apps.catalog.models import Item
from apps.core.auth_views import error_response
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.rbac.permission import require_role
from . import orders, pickup, qr, queries
from .models import AssetCard, Inventory


class MobileFilter(forms.Form):
    q = forms.CharField(max_length=100, required=False)
    stock_page = forms.IntegerField(min_value=1, required=False)
    asset_page = forms.IntegerField(min_value=1, required=False)


class LabelFilter(forms.Form):
    q = forms.CharField(max_length=100, required=False)
    item_page = forms.IntegerField(min_value=1, required=False)
    asset_page = forms.IntegerField(min_value=1, required=False)


def scan_principal(request):
    if getattr(request, "auth_error", None):
        raise request.auth_error
    p = getattr(request, "principal", None)
    if p is None:
        return None
    p = orders.identity(p)
    if p.role not in {"admin", "user"}:
        raise BusinessError("FORBIDDEN", "无权领取", 403)
    return p


def pickup_context(request, token, p):
    kind, obj = queries.resolve_scan(token, tenant_id=p.tenant_id if p else None)
    context = {
        "principal": p,
        "kind": kind,
        "token": token,
        "name": obj.item.name if kind == "asset" else obj.name,
        "school_name": obj.tenant.name,
        "tenant_id": obj.tenant_id,
        "code": obj.asset_no if kind == "asset" else obj.code,
        "unit": "件" if kind == "asset" else obj.unit,
        "form": pickup.PickupForm(),
        "idempotency_key": str(uuid4()),
    }
    if kind == "asset":
        context.update(
            available=1 if obj.status == "in_stock" and obj.location.active else 0,
            locations=obj.location.name,
            status_label=queries.STATUSES[obj.status],
        )
    else:
        stocks = Inventory.objects.filter(
            tenant_id=obj.tenant_id,
            item_id=obj.pk,
            quality="available",
            location__active=True,
            quantity__gt=0,
        )
        context.update(
            available=stocks.aggregate(n=Sum("quantity"))["n"] or 0,
            locations="、".join(
                stocks.order_by("location_id").values_list("location__name", flat=True)
            ),
        )
    return context


@require_http_methods(["GET", "POST"])
def html_pickup(request, token):
    try:
        require_runtime()
        blocked = None
        try:
            p = scan_principal(request)
        except BusinessError as error:
            if request.method != "GET":
                raise
            p, blocked = getattr(request, "principal", None), error
        context = pickup_context(request, token, p)
        if p is None and blocked is None:
            try:
                pickup.anonymous_enabled(context.pop("tenant_id"))
            except BusinessError as error:
                blocked = error
        context.pop("tenant_id", None)
        if blocked:
            context.update(
                login_blocked=True,
                login_error=blocked.message,
                login_url="/auth/change-password/"
                if blocked.code == "PASSWORD_CHANGE_REQUIRED"
                else "/auth/login/",
            )
        status = 200
        if request.method == "POST":
            data = request.POST.dict()
            key = data.pop("idempotency_key", None)
            data.pop("csrfmiddlewaretoken", None)
            context.update(form=pickup.PickupForm(data), idempotency_key=key)
            try:
                result, replay = pickup.submit(p, token, data, key)
                context.update(success=result, replayed=replay)
            except BusinessError as error:
                context.update(error=error.message)
                status = error.status
        response = render(request, "pickup/pickup.html", context, status=status)
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
    except BusinessError as error:
        return error_response(error)


@csrf_exempt
@require_http_methods(["POST"])
def api_pickup(request, token):
    try:
        require_runtime()
        p = scan_principal(request)
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            if CsrfViewMiddleware(lambda r: None).process_view(
                request, lambda r: None, (), {}
            ):
                raise BusinessError("CSRF_REJECTED", "CSRF校验失败", 403)
        if request.content_type != "application/json":
            raise BusinessError("INVALID_CONTENT_TYPE", "请提交JSON请求", 415)
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeError):
            raise BusinessError("INVALID_JSON", "JSON格式错误", 422) from None
        result, replay = pickup.submit(
            p, token, data, request.headers.get("Idempotency-Key")
        )
        response = JsonResponse(result, status=200 if replay else 201)
        response["Idempotency-Replayed"] = "true" if replay else "false"
        response["Cache-Control"] = "private, no-store"
        return response
    except BusinessError as error:
        return error_response(error)


@require_GET
@require_role("admin", "user")
def html_inventory_mobile(request):
    try:
        # Independent pagination parameters share the original authorized queries.
        data = orders.validate(MobileFilter(request.GET), request.GET)
        stock_params = {"q": data["q"], "page": data["stock_page"] or 1}
        asset_params = {"q": data["q"], "page": data["asset_page"] or 1}
        stocks, _ = queries.stock(request.principal, stock_params)
        assets, _ = queries.assets(request.principal, asset_params)
        return render(
            request,
            "pickup/inventory_mobile.html",
            {
                "principal": request.principal,
                "stocks": stocks,
                "assets": assets,
                "q": request.GET.get("q", ""),
            },
        )
    except BusinessError as error:
        return error_response(error)


@require_GET
@require_role("admin")
def api_qr_png(request, token):
    try:
        queries.resolve_scan(token, tenant_id=request.principal.tenant_id)
        url = request.build_absolute_uri(f"/s/pickup/{token}/")
        response = HttpResponse(qr.qr_png(url), content_type="image/png")
        response["X-Content-Type-Options"] = "nosniff"
        return response
    except BusinessError as error:
        return error_response(error)


@require_http_methods(["GET", "POST"])
@require_role("admin")
def html_labels(request):
    try:
        p = request.principal
        data = orders.validate(LabelFilter(request.GET), request.GET)
        notice = None
        if request.method == "POST":
            result, _ = qr.generate_item_tokens(p, request.POST.get("idempotency_key"))
            notice = (
                f"本批已生成 {result['generated']} 个耗材标签；可继续为剩余记录生成。"
            )
        items = Item.objects.filter(
            tenant_id=p.tenant_id, type="consumable", active=True
        )
        assets = AssetCard.objects.filter(
            tenant_id=p.tenant_id, item__active=True
        ).select_related("item")
        if data["q"]:
            items = items.filter(
                Q(name__icontains=data["q"]) | Q(code__icontains=data["q"])
            )
            assets = assets.filter(
                Q(asset_no__icontains=data["q"])
                | Q(item__name__icontains=data["q"])
                | Q(item__code__icontains=data["q"])
            )
        item_page = queries.page(
            items.order_by("id"),
            data["item_page"] or 1,
            lambda row: {"name": row.name, "code": row.code, "token": row.qr_token},
        )
        asset_page = queries.page(
            assets.order_by("id"),
            data["asset_page"] or 1,
            lambda row: {
                "name": row.item.name,
                "code": row.asset_no,
                "token": row.qr_token,
            },
        )
        return render(
            request,
            "pickup/labels.html",
            {
                "principal": p,
                "items": item_page,
                "assets": asset_page,
                "q": data["q"] or "",
                "idempotency_key": str(uuid4()),
                "notice": notice,
            },
        )
    except BusinessError as error:
        return error_response(error)
