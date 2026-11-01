import json
from uuid import uuid4
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from apps.inventory.pickup_views import scan_principal
from apps.core.auth_views import error_response
from apps.catalog.models import Item
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.rbac.permission import require_role
from . import services


@csrf_exempt
@require_http_methods(["POST"])
def api_write(request, pk=None, action=None):
    try:
        require_runtime()
        p = scan_principal(request)
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            failure = CsrfViewMiddleware(lambda r: None).process_view(
                request, lambda r: None, (), {}
            )
            if failure:
                return failure
        if request.content_type != "application/json":
            raise BusinessError("INVALID_JSON", "请使用JSON", 422)
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeError):
            raise BusinessError("INVALID_JSON", "JSON格式错误", 422) from None
        if not isinstance(data, dict):
            raise BusinessError("INVALID_FIELDS", "参数须为对象", 422)
        key = request.headers.get("Idempotency-Key")
        if action == "decide":
            if set(data) - {"action", "reason"}:
                raise BusinessError("INVALID_FIELDS", "未知字段", 422)
            result, replay = services.decide(
                p, pk, data.get("action"), key, data.get("reason", "")
            )
        elif action == "fulfill":
            if set(data) - {"location_id", "quality", "supplier_id", "unit_price"}:
                raise BusinessError("INVALID_FIELDS", "未知字段", 422)
            result, replay = services.fulfill(
                p,
                pk,
                key,
                data.get("location_id"),
                data.get("quality", "available"),
                data.get("supplier_id"),
                data.get("unit_price"),
            )
        else:
            result, replay = services.requisition_submit(p, data, key)
        response = JsonResponse(result, status=200 if replay else 201)
        response["Idempotency-Replayed"] = "true" if replay else "false"
        response["Cache-Control"] = "private, no-store"
        return response
    except BusinessError as error:
        return error_response(error)


@require_http_methods(["GET", "POST"])
def html_new(request):
    try:
        require_runtime()
        p = scan_principal(request)
        token = request.GET.get("token", "")
        result = None
        if request.method == "POST":
            data = {
                k: request.POST.get(k, "")
                for k in ("item_id", "qty", "reason", "name", "employee_no", "token")
            }
            result, _ = services.requisition_submit(
                p, data, request.POST.get("idempotency_key")
            )
        items = (
            Item.objects.filter(tenant_id=p.tenant_id, active=True).order_by("name")
            if p
            else []
        )
        return render(
            request,
            "procurement/new.html",
            dict(
                principal=p,
                items=items,
                item=request.GET.get("item", ""),
                token=token,
                key=uuid4().hex,
                result=result,
            ),
        )
    except BusinessError as error:
        return error_response(error)


@require_http_methods(["GET"])
@require_role("admin", "user")
def html_list(request, admin=False):
    if admin and request.principal.role != "admin":
        raise BusinessError("FORBIDDEN", "仅管理员可查看", 403)
    rows = services.visible(request.principal)
    status = request.GET.get("status", "")
    if status:
        rows = rows.filter(status=status)
    page = Paginator(rows.order_by("-id"), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "procurement/list.html",
        dict(principal=request.principal, page=page, key=uuid4().hex),
    )


@require_http_methods(["POST"])
@require_role("admin")
def html_action(request, pk, action):
    key = request.POST.get("idempotency_key")
    if action == "decide":
        services.decide(
            request.principal,
            pk,
            request.POST.get("action"),
            key,
            request.POST.get("reason", ""),
        )
    else:
        services.fulfill(
            request.principal,
            pk,
            key,
            request.POST.get("location_id"),
            request.POST.get("quality", "available"),
            request.POST.get("supplier_id"),
            request.POST.get("unit_price"),
        )
    return redirect("/manage/requisitions/")
