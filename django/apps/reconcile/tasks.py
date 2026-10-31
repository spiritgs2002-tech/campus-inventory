"""Internal operations tasks. Scope comes from the deployment allowlist."""

import logging
from celery import shared_task
from django.conf import settings
from shared.errors import BusinessError
from . import maintenance

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="maintenance.cleanup",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=540,
    time_limit=600,
    max_retries=3,
)
def cleanup(self, tenant_id, kind, batch_size=25):
    if not settings.MAINTENANCE_ENABLED:
        return {"ok": False, "error": "MAINTENANCE_DISABLED"}
    from apps.attachments.cleanup import cleanup_evidence, cleanup_orphans

    functions = {
        "sessions": maintenance.cleanup_sessions,
        "responses": maintenance.cleanup_responses,
        "evidence": cleanup_evidence,
        "orphans": cleanup_orphans,
    }
    try:
        if kind not in functions:
            raise BusinessError("INVALID_MAINTENANCE_KIND", "维护类型无效", 422)
        result = functions[kind](tenant_id, self.request.id, batch_size=batch_size)
        if isinstance(result, tuple):
            result, replay = result
            result = {**result, "replayed": replay, "ok": True}
        if not result.get("ok", True):
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=10 * (self.request.retries + 1))
            logger.error(
                "Maintenance exhausted retries: tenant=%s kind=%s", tenant_id, kind
            )
        return result
    except BusinessError as error:
        logger.error(
            "Maintenance rejected: tenant=%s kind=%s code=%s",
            tenant_id,
            kind,
            error.code,
        )
        if error.status == 503 and self.request.retries < self.max_retries:
            raise self.retry(exc=error, countdown=10 * (self.request.retries + 1))
        return {"ok": False, "error": error.code}


@shared_task(name="maintenance.reconcile", soft_time_limit=540, time_limit=600)
def reconcile():
    if not settings.MAINTENANCE_ENABLED:
        return {"ok": False, "error": "MAINTENANCE_DISABLED"}
    from .services import run

    try:
        result = run()
    except BusinessError as error:
        logger.error("Periodic reconcile rejected: %s", error.code)
        return {"ok": False, "error": error.code}
    if not result["ok"]:
        logger.error("Periodic reconcile found inconsistent data")
    return result
