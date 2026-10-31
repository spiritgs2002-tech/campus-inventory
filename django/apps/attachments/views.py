from io import BytesIO
import json
from uuid import uuid4
from django.core.paginator import Paginator
from django.http import FileResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from apps.core.auth_views import error_response
from apps.inventory import orders
from shared.errors import BusinessError
from shared.rbac.permission import require_role
from . import services
from .forms import UploadForm, AssociationForm, ReasonForm, CSVForm


def binary_response(principal, pk):
    obj, data = services.download(principal, pk)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}.get(
        obj.content_type, ".bin"
    )
    response = FileResponse(
        BytesIO(data),
        content_type=obj.content_type,
        as_attachment=True,
        filename=f"evidence-{obj.pk}{suffix}",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def json_payload(request):
    if request.content_type != "application/json":
        raise BusinessError("INVALID_CONTENT_TYPE", "请提交JSON", 415)
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeError):
        raise BusinessError("INVALID_JSON", "JSON格式错误", 422) from None
    if not isinstance(data, dict):
        raise BusinessError("INVALID_FIELDS", "请求须为对象", 422)
    return data


@csrf_exempt
@require_role("admin", "user")
def api_attachments(request, pk=None, action=None, scrap_id=None):
    try:
        p = request.principal
        if request.method == "GET":
            if action == "download":
                return binary_response(p, pk)
            if action or scrap_id:
                return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
            if pk:
                return JsonResponse({"record": services.record(services.get(p, pk))})
            if "ids" in request.GET:
                if set(request.GET) != {"ids"}:
                    raise BusinessError("INVALID_FIELDS", "批量查询仅接受ids", 422)
                try:
                    ids = [int(value) for value in request.GET["ids"].split(",")]
                except ValueError:
                    raise BusinessError("INVALID_IDS", "附件ID无效", 422) from None
                return JsonResponse({"records": services.raw_metadata(p, ids)})
            if set(request.GET) - {"page"}:
                raise BusinessError("INVALID_FIELDS", "查询参数无效", 422)
            page = Paginator(services.visible(p).order_by("-id"), 25).get_page(
                request.GET.get("page")
            )
            return JsonResponse(
                {
                    "records": [services.record(row) for row in page],
                    "page": page.number,
                    "pages": page.paginator.num_pages,
                }
            )
        if request.method not in {"POST", "DELETE"}:
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        if not request.headers.get("Authorization", "").startswith(
            "Bearer "
        ) and CsrfViewMiddleware(lambda r: None).process_view(
            request, lambda r: None, (), {}
        ):
            raise BusinessError("CSRF_REJECTED", "CSRF校验失败", 403)
        key = request.headers.get("Idempotency-Key")
        if (
            action in {None, "import"}
            and not pk
            and not scrap_id
            and request.method == "POST"
        ):
            if request.content_type != "multipart/form-data":
                raise BusinessError(
                    "INVALID_CONTENT_TYPE", "请使用multipart上传文件", 415
                )
            allowed = {"sha256"} if action is None else set()
            # Multipart parsing may stop early and close partial temporary files.
            # Reject before inspecting any UploadedFile from that partial parse.
            request.POST
            if getattr(request, "evidence_upload_rejected", False):
                raise BusinessError("INVALID_FILE_SIZE", "文件过大或包含多个文件", 422)
            if (
                set(request.POST) - allowed
                or set(request.FILES) != {"file"}
                or len(request.FILES.getlist("file")) != 1
            ):
                raise BusinessError("INVALID_FIELDS", "上传字段无效", 422)
            form = (UploadForm if action is None else CSVForm)(
                data=request.POST, files=request.FILES
            )
            if not form.is_valid():
                raise BusinessError("INVALID_FILE", "上传参数无效", 422)
            if action == "import":
                result, replayed = services.import_csv(
                    p, key, form.cleaned_data["file"].read(65537)
                )
            else:
                result, replayed = services.upload(
                    p, key, form.cleaned_data["file"], form.cleaned_data["sha256"]
                )
        elif scrap_id:
            data = json_payload(request)
            if set(data) - {"reason"}:
                raise BusinessError("INVALID_FIELDS", "关联参数无效", 422)
            reason = data.get("reason", "")
            if not isinstance(reason, str) or len(reason) > 2000:
                raise BusinessError("INVALID_FIELDS", "说明无效", 422)
            result, replayed = services.associate(
                p, key, scrap_id, pk, withdraw=request.method == "DELETE", reason=reason
            )
        elif pk and action == "hold" and request.method == "POST":
            data = orders.validate(
                ReasonForm(data=json_payload(request)), json_payload(request)
            )
            result, replayed = services.legal_hold(p, key, pk, data["reason"])
        else:
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        response = JsonResponse(result, status=200 if replayed or pk or action else 201)
        response["Idempotency-Replayed"] = "true" if replayed else "false"
        return response
    except BusinessError as error:
        return error_response(error)


@require_role("admin", "user")
def html_attachments(request, pk=None, action=None, scrap_id=None):
    try:
        p = request.principal
        if request.method == "POST":
            request.POST
            if getattr(request, "evidence_upload_rejected", False):
                raise BusinessError("INVALID_FILE_SIZE", "文件过大或包含多个文件", 422)
        if action == "download" and request.method == "GET":
            return binary_response(p, pk)
        if request.method not in {"GET", "POST"}:
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        ctx = {"principal": p, "idempotency_key": str(uuid4()), "action": action}
        status = 200
        if action is None:
            if request.method != "GET":
                return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
            page = Paginator(services.visible(p).order_by("-id"), 25).get_page(
                request.GET.get("page")
            )
            ctx.update(page=page, records=[services.record(row) for row in page])
            template = "attachments/list.html"
        else:
            if action not in {"upload", "link", "withdraw", "hold", "import"}:
                raise BusinessError("NOT_FOUND", "附件页面不存在", 404)
            if pk:
                ctx["record"] = services.record(services.get(p, pk))
            if action == "hold" and p.role != "admin":
                raise BusinessError("FORBIDDEN", "保全仅限管理员", 403)
            cls = (
                UploadForm
                if action == "upload"
                else AssociationForm
                if action == "link"
                else CSVForm
                if action == "import"
                else ReasonForm
            )
            kwargs = (
                {"principal": p, "initial": {"scrap_id": request.GET.get("scrap_id")}}
                if action == "link"
                else {}
            )
            form = cls(
                request.POST if request.method == "POST" else None,
                request.FILES if request.method == "POST" else None,
                **kwargs,
            )
            if request.method == "POST" and form.is_valid():
                try:
                    key = request.POST.get("idempotency_key")
                    data = form.cleaned_data
                    if action == "upload":
                        services.upload(p, key, data["file"], data["sha256"])
                    elif action == "import":
                        services.import_csv(p, key, data["file"].read(65537))
                    elif action == "link":
                        services.associate(
                            p,
                            key,
                            data["scrap_id"],
                            data["attachment_id"],
                            reason=data["reason"],
                        )
                    elif action == "withdraw":
                        services.associate(
                            p, key, scrap_id, pk, withdraw=True, reason=data["reason"]
                        )
                    else:
                        services.legal_hold(p, key, pk, data["reason"])
                    return redirect(
                        f"/scrap/{scrap_id}/" if scrap_id else "/attachments/"
                    )
                except BusinessError as error:
                    form.add_error(None, error.message)
                    status = error.status
            elif request.method == "POST":
                status = 422
            ctx["form"] = form
            template = "attachments/form.html"
        ctx["fragment_template"] = template
        return render(
            request,
            template
            if request.headers.get("HX-Request") == "true"
            else "attachments/page.html",
            ctx,
            status=status,
        )
    except BusinessError as error:
        return error_response(error)
