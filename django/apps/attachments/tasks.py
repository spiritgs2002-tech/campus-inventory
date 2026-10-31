from celery import shared_task
from shared.auth.backends import authorize_claims
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from . import services


@shared_task(
    name="attachments.verify_integrity",
    serializer="json",
    acks_late=True,
    soft_time_limit=20,
    time_limit=30,
)
def verify_integrity(scope, attachment_id):
    """Revalidate the initiating session family; never trust an ambient request tenant."""
    try:
        require_runtime()
        if (
            not isinstance(scope, dict)
            or set(scope) != {"tid", "sub", "fid"}
            or type(attachment_id) is not int
        ):
            raise BusinessError("INVALID_TASK_SCOPE", "任务归属参数无效", 422)
        p = authorize_claims(scope)
        obj, data = services.download(p, attachment_id)
        return {
            "ok": True,
            "attachment_id": obj.pk,
            "sha256": obj.sha256,
            "size_bytes": len(data),
        }
    except BusinessError as error:
        return {"ok": False, "error": error.code}
