import json
from uuid import uuid4
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from .errors import BusinessError
from .rbac.permission import require_role
from apps.core.auth_views import error_response
from apps.core import services as core
from apps.catalog import services as catalog


def provider(resource, path=""):
    is_catalog = path.startswith(("/catalog/", "/api/v1/catalog/"))
    if path and (
        (is_catalog and resource not in catalog.RESOURCES)
        or (not is_catalog and resource not in core.RESOURCES)
    ):
        raise BusinessError("NOT_FOUND", "资源不存在", 404)
    if resource in core.RESOURCES:
        return core
    if resource in catalog.RESOURCES:
        return catalog
    raise BusinessError("NOT_FOUND", "资源不存在", 404)


def _allowed(principal, resource, write=False):
    if (write or resource in core.RESOURCES) and principal.role != "admin":
        raise BusinessError("FORBIDDEN", "仅管理员可操作", 403)


def _query(request, service, resource):
    rows = service.queryset(resource, request.principal.tenant_id)
    if request.principal.role != "admin":
        rows = rows.filter(active=True)
    search = request.GET.get("q", "").strip()[:120]
    if search:
        rows = rows.filter(name__icontains=search)
    return rows.order_by("id")


def _save(request, service, resource, pk, data, key):
    _allowed(request.principal, resource, True)
    return service.save(request.principal, resource, pk, key, data)


@csrf_exempt
@require_role("admin", "user")
def api_resource(request, resource, pk=None):
    try:
        service = provider(resource, request.path)
        _allowed(request.principal, resource, request.method != "GET")
        if resource == "tenant":
            if pk is not None and pk != request.principal.tenant_id:
                raise BusinessError("NOT_FOUND", "记录不存在", 404)
            pk = request.principal.tenant_id
        if request.method == "GET":
            if pk:
                record = service.serialize(
                    resource,
                    service.get_instance(resource, request.principal.tenant_id, pk),
                )
                return JsonResponse({"record": record})
            page = Paginator(_query(request, service, resource), 25).get_page(
                request.GET.get("page")
            )
            return JsonResponse(
                {
                    "records": [service.serialize(resource, row) for row in page],
                    "page": page.number,
                    "pages": page.paginator.num_pages,
                }
            )
        # Cookie authenticated writes still need CSRF validation; bearer requests do not use cookies.
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            from django.middleware.csrf import CsrfViewMiddleware

            if CsrfViewMiddleware(lambda r: None).process_view(
                request, lambda r: None, (), {}
            ):
                raise BusinessError("CSRF_REJECTED", "CSRF校验失败", 403)
        if request.method not in {"POST", "PUT", "DELETE"}:
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        if request.method == "PUT" and pk is None:
            raise BusinessError("INVALID_DATA", "更新操作必须指定记录", 422)
        if request.content_type != "application/json":
            raise BusinessError("INVALID_CONTENT_TYPE", "请提交 JSON 请求", 415)
        try:
            data = json.loads(request.body or b"{}")
        except (ValueError, UnicodeError):
            raise BusinessError("INVALID_JSON", "JSON格式错误") from None
        if not isinstance(data, dict):
            raise BusinessError("INVALID_DATA", "请求必须为对象", 422)
        if request.method == "DELETE":
            if pk is None or resource == "tenant" or set(data) - {"reason"}:
                raise BusinessError("INVALID_DATA", "仅可停用指定主数据记录", 422)
            obj = service.get_instance(resource, request.principal.tenant_id, pk)
            form = service.form_for(resource, request.principal.tenant_id, obj)
            payload = {
                **form.initial,
                "active": False,
                "reason": data.get("reason", ""),
            }
        else:
            payload = dict(data)
            if pk is None:
                payload.setdefault("active", True)
        result, replayed = _save(
            request,
            service,
            resource,
            pk,
            payload,
            request.headers.get("Idempotency-Key"),
        )
        response = JsonResponse(result, status=200 if pk or replayed else 201)
        response["Idempotency-Replayed"] = "true" if replayed else "false"
        return response
    except BusinessError as error:
        return error_response(error)


@require_role("admin", "user")
def html_resource(request, resource, pk=None, action=None):
    try:
        service = provider(resource, request.path)
        _allowed(request.principal, resource, bool(action))
        if resource == "tenant":
            if pk is not None and pk != request.principal.tenant_id:
                raise BusinessError("NOT_FOUND", "记录不存在", 404)
            pk = request.principal.tenant_id
            action = "edit"
        obj = (
            service.get_instance(resource, request.principal.tenant_id, pk)
            if pk
            else None
        )
        if request.method not in {"GET", "POST"}:
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        is_fragment = request.headers.get("HX-Request") == "true"
        context = {
            "resource": resource,
            "label": service.RESOURCES[resource][2],
            "principal": request.principal,
            "idempotency_key": str(uuid4()),
            "base_url": f"/manage/{resource}/"
            if service is core
            else f"/catalog/{resource}/",
        }
        if action:
            if action not in {"new", "edit", "deactivate"}:
                raise BusinessError("NOT_FOUND", "页面不存在", 404)
            if action == "deactivate":
                if request.method != "POST":
                    return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
                initial = service.form_for(
                    resource, request.principal.tenant_id, obj
                ).initial
                payload = {
                    **initial,
                    "active": False,
                    "reason": request.POST.get("reason", ""),
                }
            else:
                payload = request.POST.dict()
                payload.pop("csrfmiddlewaretoken", None)
                payload.pop("idempotency_key", None)
            form = service.form_for(
                resource,
                request.principal.tenant_id,
                obj,
                data=payload if request.method == "POST" else None,
            )
            status = 200
            if request.method == "POST":
                try:
                    _save(
                        request,
                        service,
                        resource,
                        pk,
                        payload,
                        request.headers.get("Idempotency-Key")
                        or request.POST.get("idempotency_key"),
                    )
                    if not is_fragment:
                        return redirect(
                            f"/manage/{resource}/"
                            if resource in core.RESOURCES
                            else f"/catalog/{resource}/"
                        )
                    action = None
                except BusinessError as error:
                    if error.status in {401, 403, 404, 503}:
                        raise
                    form.add_error(None, error.message)
                    status = error.status
            if action:
                context.update(form=form, object=obj)
                return render(
                    request,
                    "master/form_fragment.html" if is_fragment else "master/form.html",
                    context,
                    status=status,
                )
        page = Paginator(_query(request, service, resource), 25).get_page(
            request.GET.get("page")
        )
        context.update(
            page=page, records=[service.serialize(resource, row) for row in page]
        )
        return render(
            request,
            "master/list_fragment.html" if is_fragment else "master/list.html",
            context,
        )
    except BusinessError as error:
        return error_response(error)
