from django.core.paginator import Paginator
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect
from django.views.decorators.http import require_GET, require_POST
from shared.rbac.permission import require_role
from .services import owned, mark_read


@require_GET
@require_role("admin", "user")
def html_notifications(request):
    page = Paginator(owned(request.principal).order_by("-id"), 25).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "notifications/list.html",
        {"principal": request.principal, "page": page},
    )


@csrf_exempt
@require_POST
@require_role("admin", "user")
def api_notification_read(request, pk):
    if not request.headers.get("Authorization", "").startswith("Bearer "):
        failure = CsrfViewMiddleware(lambda r: None).process_view(
            request, lambda r: None, (), {}
        )
        if failure:
            return failure
    result = mark_read(request.principal, pk)
    return (
        redirect("/s/notifications/")
        if request.content_type == "application/x-www-form-urlencoded"
        else JsonResponse(result)
    )
