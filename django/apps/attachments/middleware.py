from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http import JsonResponse


class EvidenceLimitHandler(FileUploadHandler):
    """Stop oversized bodies before Django's temporary-file handler fills disk."""

    def __init__(self, request, limit):
        super().__init__(request)
        self.limit, self.received, self.files = limit, 0, 0

    def reject(self):
        self.request.evidence_upload_rejected = True
        raise StopUpload(connection_reset=True)

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.files += 1
        if self.files > 1:
            self.reject()

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > self.limit:
            self.reject()
        return raw_data

    def file_complete(self, file_size):
        return None


class EvidenceUploadLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.startswith(
            ("/attachments/", "/api/v1/attachments/")
        ):
            from django.conf import settings

            limit = (
                65536
                if request.path.endswith("/import/")
                else settings.ATTACHMENT_MAX_BYTES
            )
            request.upload_handlers.insert(0, EvidenceLimitHandler(request, limit))
        response = self.get_response(request)
        if getattr(request, "evidence_upload_rejected", False):
            return JsonResponse(
                {
                    "error": {
                        "code": "INVALID_FILE_SIZE",
                        "message": "文件过大或一次上传了多个文件",
                    }
                },
                status=422,
            )
        return response
