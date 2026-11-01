import json
from uuid import uuid4
from django import forms
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from apps.core.auth_views import error_response
from apps.core.models import User
from apps.inventory import queries
from apps.inventory.pickup_views import scan_principal
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.rbac.permission import require_role
from . import services
from .forms import (
    TicketForm,
    STATUSES,
    TARGETS,
    PRIORITIES,
    DispatchForm,
    TransitionForm,
    clean_payload,
)


def present(row):
    row.status_label = dict(STATUSES)[row.status]
    row.target_label = dict(TARGETS)[row.target_type]
    row.priority_label = dict(PRIORITIES)[row.priority]
    return row


def private(response):
    response["Cache-Control"] = "private, no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


@csrf_exempt
@require_POST
def api_write(request, pk=None, operation=None):
    try:
        require_runtime()
        p = scan_principal(request)
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            if CsrfViewMiddleware(lambda r: None).process_view(
                request, lambda r: None, (), {}
            ):
                raise BusinessError("CSRF_REJECTED", "CSRF校验失败", 403)
        if request.content_type != "application/json":
            raise BusinessError("INVALID_CONTENT_TYPE", "请使用JSON", 415)
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeError):
            raise BusinessError("INVALID_JSON", "JSON格式错误", 422) from None
        key = request.headers.get("Idempotency-Key")
        if operation == "dispatch":
            data = clean_payload(DispatchForm, payload)
            result, replay = services.repair_transition(
                p, pk, "dispatch", key, assigned_to=data["assigned_to"]
            )
        elif operation == "status":
            data = clean_payload(TransitionForm, payload)
            result, replay = services.repair_transition(
                p, pk, data["action"], key, reason=data["reason"]
            )
        else:
            result, replay = services.repair_submit(p, payload, key)
        response = JsonResponse(
            result, status=201 if pk is None and not replay else 200
        )
        response["Idempotency-Replayed"] = "true" if replay else "false"
        return private(response)
    except BusinessError as error:
        return private(error_response(error))


@require_GET
@require_role("admin", "user")
def api_detail(request, pk):
    return JsonResponse(
        {"document": services.record(services.detail(request.principal, pk))}
    )


@require_http_methods(["GET", "POST"])
def html_new(request):
    try:
        require_runtime()
        p = scan_principal(request)
        initial = {
            name: request.GET[name]
            for name in ("target_type", "target_id", "token")
            if name in request.GET
        }
        token = request.GET.get("asset") or initial.get("token")
        if token:
            kind, obj = queries.resolve_scan(
                token, tenant_id=p.tenant_id if p else None
            )
            initial = {
                "target_type": kind,
                "target_id": obj.pk,
                "token": token,
                **initial,
            }
        key = (
            request.POST.get("idempotency_key")
            if request.method == "POST"
            else uuid4().hex
        )
        form = TicketForm(
            request.POST if request.method == "POST" else None,
            initial={"priority": "normal", **initial},
        )
        if p is not None:
            form.fields["name"].widget = forms.HiddenInput()
            form.fields["employee_no"].widget = forms.HiddenInput()
        status = 200
        if request.method == "POST":
            request.POST
            if getattr(request, "evidence_upload_rejected", False):
                raise BusinessError("INVALID_FILE_SIZE", "文件过大或包含多张照片", 422)
            if (
                set(request.POST)
                - {*TicketForm.base_fields, "csrfmiddlewaretoken", "idempotency_key"}
                or set(request.FILES) - {"photo"}
                or len(request.FILES.getlist("photo")) > 1
            ):
                raise BusinessError("INVALID_FIELDS", "表单包含未知字段或多张照片", 422)
            try:
                payload = {
                    name: request.POST.get(name, "") for name in TicketForm.base_fields
                }
                result, replay = services.repair_submit(
                    p, payload, key, file=request.FILES.get("photo")
                )
                return private(
                    render(
                        request,
                        "repair/success.html",
                        {
                            "principal": p,
                            "document": result["document"],
                            "replay": replay,
                        },
                    )
                )
            except BusinessError as error:
                form.is_valid()
                form.add_error(None, error.message)
                status = error.status
        return private(
            render(
                request,
                "repair/new.html",
                {"principal": p, "form": form, "key": key},
                status=status,
            )
        )
    except BusinessError as error:
        return private(error_response(error))


class ListFilter(forms.Form):
    status = forms.ChoiceField(choices=[("", "全部"), *STATUSES], required=False)
    page = forms.IntegerField(min_value=1, required=False)


@require_GET
@require_role("admin", "user")
def html_list(request, admin=False):
    p = services.identity(request.principal)
    if admin and p.role != "admin":
        raise BusinessError("FORBIDDEN", "仅管理员可处理报修", 403)
    data = clean_payload(ListFilter, request.GET.dict())
    rows = services.visible(p)
    if data["status"]:
        rows = rows.filter(status=data["status"])
    page = Paginator(rows.order_by("-id"), 25).get_page(data["page"] or 1)
    page.object_list = [present(row) for row in page.object_list]
    users = (
        User.objects.filter(tenant_id=p.tenant_id, active=True).order_by("name", "id")
        if admin
        else []
    )
    return render(
        request,
        "repair/list.html",
        {
            "principal": p,
            "page": page,
            "filter": ListFilter(request.GET),
            "status": data["status"],
            "users": users,
            "admin": admin,
            "key": uuid4().hex,
        },
    )


@require_GET
@require_role("admin", "user")
def html_detail(request, pk):
    return render(
        request,
        "repair/detail.html",
        {
            "principal": request.principal,
            "ticket": present(services.detail(request.principal, pk)),
        },
    )


@require_POST
@require_role("admin")
def html_action(request, pk, operation):
    key = request.POST.get("idempotency_key")
    if operation == "dispatch":
        data = clean_payload(
            DispatchForm,
            {
                k: v
                for k, v in request.POST.items()
                if k not in {"csrfmiddlewaretoken", "idempotency_key"}
            },
        )
        services.repair_transition(
            request.principal, pk, "dispatch", key, assigned_to=data["assigned_to"]
        )
    else:
        data = clean_payload(
            TransitionForm,
            {
                k: v
                for k, v in request.POST.items()
                if k not in {"csrfmiddlewaretoken", "idempotency_key"}
            },
        )
        services.repair_transition(
            request.principal, pk, data["action"], key, reason=data["reason"]
        )
    return redirect("/manage/repairs/")
