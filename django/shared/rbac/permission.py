from functools import wraps
from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect
from shared.db.runtime import require_runtime
from shared.errors import BusinessError


def require_role(*roles, allow_password_change=False):
    def navigate(request, url):
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse()
            response["HX-Redirect"] = url
            return response
        return redirect(url)

    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                principal = request.principal
                if principal is None:
                    raise BusinessError("UNAUTHENTICATED", "请先登录", 401)
                if roles and principal.role not in roles:
                    raise BusinessError("FORBIDDEN", "无权执行此操作", 403)
                if principal.must_change_password and not allow_password_change:
                    if not request.path.startswith("/api/") and request.method == "GET":
                        return navigate(request, "/auth/change-password/")
                    raise BusinessError(
                        "PASSWORD_CHANGE_REQUIRED", "请先修改初始密码", 403
                    )
                require_runtime()
                response = view(request, *args, **kwargs)
                response["Cache-Control"] = "private, no-store"
                return response
            except BusinessError as error:
                if (
                    error.status == 401
                    and request.method == "GET"
                    and not request.path.startswith("/api/")
                ):
                    return navigate(request, "/auth/login/")
                return JsonResponse(
                    {"error": {"code": error.code, "message": error.message}},
                    status=error.status,
                )

        return wrapped

    return decorate
