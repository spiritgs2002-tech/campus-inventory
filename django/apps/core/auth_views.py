import json
from uuid import uuid4
from django.conf import settings
from django import forms
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from . import auth_service
from shared.auth.tokens import decode_access
from shared.errors import BusinessError


class LoginForm(forms.Form):
    tenant_code = forms.CharField(label="学校代码", max_length=80)
    username = forms.CharField(label="用户名", max_length=80)
    password = forms.CharField(label="密码", widget=forms.PasswordInput, max_length=72)


class PasswordForm(forms.Form):
    old_password = forms.CharField(
        label="当前密码", widget=forms.PasswordInput, max_length=72
    )
    new_password = forms.CharField(
        label="新密码（至少12字符）", widget=forms.PasswordInput, max_length=72
    )


def claims_from_request(request):
    header = request.headers.get("Authorization", "")
    if header:
        if not header.startswith("Bearer "):
            raise BusinessError("UNAUTHENTICATED", "请先登录", 401)
        return decode_access(header[7:])
    claims = request.session.get("identity")
    if not claims:
        raise BusinessError("UNAUTHENTICATED", "请先登录", 401)
    return claims


def error_response(error):
    data = {"error": {"code": error.code, "message": error.message}}
    if hasattr(error, "fields"):
        data["error"]["fields"] = error.fields
    if hasattr(error, "business_refs"):
        data["error"]["business_refs"] = error.business_refs
    response = JsonResponse(data, status=error.status)
    response["Cache-Control"] = "no-store"
    return response


def _issue(request, result, as_json=True):
    body, refresh, claims = result
    request.session.cycle_key()
    request.session["identity"] = claims
    response = (
        JsonResponse(body)
        if as_json
        else redirect(
            "/auth/change-password/"
            if body["force_change_password"]
            else "/catalog/items/"
        )
    )
    response.set_cookie(
        "inventory_refresh",
        refresh,
        max_age=settings.REFRESH_TOKEN_SECONDS,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Strict",
        path="/api/v1/auth",
    )
    response["Cache-Control"] = "no-store"
    return response


def _clear(request, response):
    request.session.flush()
    response.delete_cookie("inventory_refresh", path="/api/v1/auth", samesite="Strict")
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def api_auth(request, action):
    try:
        if (
            request.headers.get("Origin") not in settings.CSRF_TRUSTED_ORIGINS
            or request.headers.get("X-Inventory-Request") != "1"
        ):
            raise BusinessError("ORIGIN_REJECTED", "请求来源或安全请求头无效", 403)
        if request.content_type != "application/json":
            raise BusinessError("INVALID_CONTENT_TYPE", "请提交 JSON 请求", 415)
        try:
            data = json.loads(request.body or b"{}")
        except (ValueError, UnicodeError):
            raise BusinessError("INVALID_JSON", "JSON格式错误") from None
        allowed = {
            "login": {"tenant_code", "username", "password", "idempotency_key"},
            "refresh": set(),
            "logout": set(),
            "change-password": {"old_password", "new_password"},
        }
        if action not in allowed:
            raise BusinessError("NOT_FOUND", "接口不存在", 404)
        if not isinstance(data, dict) or set(data) - allowed[action]:
            raise BusinessError("INVALID_FIELDS", "含不允许的字段", 422)
        key = request.headers.get("Idempotency-Key")
        if action == "login":
            form = LoginForm(data)
            if not form.is_valid():
                raise BusinessError("INVALID_DATA", "登录参数不完整", 422)
            if data.get("idempotency_key") and data["idempotency_key"] != key:
                raise BusinessError(
                    "IDEMPOTENCY_CONFLICT", "请求头与请求体幂等键不一致", 409
                )
            return _issue(request, auth_service.login(**form.cleaned_data, key=key))
        if action == "refresh":
            response = _issue(
                request,
                auth_service.refresh(request.COOKIES.get("inventory_refresh", ""), key),
            )
        elif action == "logout":
            result = auth_service.logout(claims_from_request(request), key)
            response = _clear(request, JsonResponse(result))
        else:
            form = PasswordForm(data)
            if not form.is_valid():
                raise BusinessError("INVALID_DATA", "密码参数不完整", 422)
            result = auth_service.change_password(
                claims_from_request(request), key=key, **form.cleaned_data
            )
            response = _clear(request, JsonResponse(result))
        return response
    except BusinessError as error:
        response = error_response(error)
        if error.code in {"REFRESH_REUSE_DETECTED", "UNAUTHENTICATED"}:
            _clear(request, response)
        return response


def html_auth(request, action):
    if action not in {"login", "change-password", "logout"}:
        return JsonResponse({"error": "NOT_FOUND"}, status=404)
    if action == "logout" and request.method != "POST":
        return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
    if request.method not in {"GET", "POST"}:
        return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
    form = (LoginForm if action == "login" else PasswordForm)(
        request.POST if request.method == "POST" else None
    )
    if request.method == "POST":
        try:
            key = request.POST.get("idempotency_key")
            if action == "logout":
                auth_service.logout(claims_from_request(request), key)
                return _clear(request, redirect("/auth/login/"))
            if form.is_valid():
                if action == "login":
                    return _issue(
                        request, auth_service.login(**form.cleaned_data, key=key), False
                    )
                auth_service.change_password(
                    claims_from_request(request), key=key, **form.cleaned_data
                )
                return _clear(request, redirect("/auth/login/"))
        except BusinessError as error:
            form.add_error(None, error.message)
    response = render(
        request,
        "auth/form.html",
        {"form": form, "action": action, "idempotency_key": str(uuid4())},
    )
    response["Cache-Control"] = "no-store"
    return response
