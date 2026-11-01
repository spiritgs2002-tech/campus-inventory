"""Reuse bounded evidence parsing before CSRF reads a repair multipart body."""

from django.conf import settings
from django.http import JsonResponse
from apps.attachments.middleware import EvidenceLimitHandler


class RepairUploadLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path == "/s/repair/new/":
            request.upload_handlers.insert(
                0, EvidenceLimitHandler(request, settings.ATTACHMENT_MAX_BYTES)
            )
        response = self.get_response(request)
        if getattr(request, "evidence_upload_rejected", False):
            return JsonResponse(
                {
                    "error": {
                        "code": "INVALID_FILE_SIZE",
                        "message": "文件过大或包含多张照片",
                    }
                },
                status=422,
            )
        return response
