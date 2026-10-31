import json
from uuid import uuid4
from django import forms
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from apps.catalog.models import Item, Location
from apps.core.auth_views import error_response
from apps.core.models import User
from shared.errors import BusinessError
from shared.rbac.permission import require_role
from . import assets, queries

LABELS = {"stock": "耗材库存", "ledger": "库存流水", "assets": "固定资产"}


@require_role("admin", "user")
def api_read(request, resource, pk=None, token=None):
    if request.method != "GET":
        return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
    try:
        if pk is not None or token is not None:
            result = queries.asset_detail(
                request.principal, pk=pk, token=token, params=request.GET
            )
        else:
            result, _ = getattr(queries, resource)(request.principal, request.GET)
        return JsonResponse(result)
    except BusinessError as error:
        return error_response(error)


@csrf_exempt
@require_role("admin")
def api_repair(request, pk, action):
    try:
        if request.method != "POST":
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            if CsrfViewMiddleware(lambda r: None).process_view(
                request, lambda r: None, (), {}
            ):
                raise BusinessError("CSRF_REJECTED", "CSRF校验失败", 403)
        if request.content_type != "application/json":
            raise BusinessError("INVALID_CONTENT_TYPE", "请提交JSON请求", 415)
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeError):
            raise BusinessError("INVALID_JSON", "JSON格式错误", 422) from None
        result, replayed = assets.repair(
            request.principal,
            pk,
            action,
            request.headers.get("Idempotency-Key"),
            payload,
        )
        response = JsonResponse(result)
        response["Idempotency-Replayed"] = "true" if replayed else "false"
        return response
    except BusinessError as error:
        return error_response(error)


def configure_filters(principal, resource, form):
    for name, model, label in [
        ("item_id", Item, "物资"),
        ("location_id", Location, "库位"),
        ("recipient_id", User, "责任人"),
    ]:
        if name in form.fields:
            rows = model.objects.filter(tenant_id=principal.tenant_id)
            if model is Item:
                rows = rows.filter(
                    type="fixed" if resource == "assets" else "consumable"
                )
            form.fields[name].label = label
            form.fields[name].widget = forms.Select(
                choices=[("", "全部"), *rows.order_by("id").values_list("id", "name")]
            )
    for name, label in [
        ("status", "状态"),
        ("quality", "质量"),
        ("low", "库存提示"),
        ("direction", "方向"),
    ]:
        if name in form.fields:
            form.fields[name].label = label
    form.fields.pop("page", None)


@require_role("admin", "user")
def html_read(request, resource, pk=None, token=None):
    if request.method != "GET":
        return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
    try:
        p = request.principal
        context = {
            "principal": p,
            "resource": resource,
            "label": LABELS[resource],
            "idempotency_key": str(uuid4()),
        }
        if pk is not None or token is not None:
            context.update(
                queries.asset_detail(p, pk=pk, token=token, params=request.GET)
            )
            page_data = context["events"]
            fragment = "inventory/asset_detail.html"
        else:
            result, form = getattr(queries, resource)(p, request.GET)
            configure_filters(p, resource, form)
            context.update(result, filter_form=form)
            page_data = result
            fragment = "inventory/list.html"
        for direction, number in [
            ("previous_url", page_data["page"] - 1),
            ("next_url", page_data["page"] + 1),
        ]:
            if 1 <= number <= page_data["pages"]:
                params = request.GET.copy()
                params["page"] = number
                context[direction] = "?" + params.urlencode()
        context["fragment_template"] = fragment
        return render(
            request,
            fragment
            if request.headers.get("HX-Request") == "true"
            else "inventory/page.html",
            context,
        )
    except BusinessError as error:
        return error_response(error)


@require_role("admin")
def html_repair(request, pk, action):
    try:
        if request.method not in {"GET", "POST"}:
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        if action not in {"repair_start", "repair_finish"}:
            raise BusinessError("NOT_FOUND", "维修页面不存在", 404)
        data = queries.asset_detail(request.principal, pk=pk)
        form = assets.RepairForm(request.POST if request.method == "POST" else None)
        if action == "repair_start":
            form.fields["location_id"].widget = forms.HiddenInput()
        else:
            form.fields["location_id"].widget = forms.Select(
                choices=[
                    ("", "保持原库位"),
                    *Location.objects.filter(
                        tenant_id=request.principal.tenant_id, active=True
                    )
                    .order_by("id")
                    .values_list("id", "name"),
                ]
            )
        status = 200
        if request.method == "POST" and form.is_valid():
            try:
                assets.repair(
                    request.principal,
                    pk,
                    action,
                    request.POST.get("idempotency_key"),
                    form.cleaned_data,
                )
                return redirect(f"/assets/{pk}/")
            except BusinessError as error:
                form.add_error(None, error.message)
                status = error.status
        elif request.method == "POST":
            status = 422
        context = {
            "principal": request.principal,
            "form": form,
            "record": data["record"],
            "action": action,
            "idempotency_key": str(uuid4()),
            "fragment_template": "inventory/repair.html",
        }
        return render(request, "inventory/page.html", context, status=status)
    except BusinessError as error:
        return error_response(error)
