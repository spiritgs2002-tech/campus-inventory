import logging
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

logger = logging.getLogger(__name__)


@transaction.non_atomic_requests
@require_safe
def index(request):
    return render(request, "core/index.html")


@transaction.non_atomic_requests
@require_safe
def health(request):
    """Liveness only; database and broker readiness are separate checks."""
    return JsonResponse({"status": "ok", "phase": "P0"})


@transaction.non_atomic_requests
@require_safe
def ready(request):
    """Bounded dependency checks; never return connection strings or exceptions."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as error:
        checks["database"] = "error"
        logger.warning(
            "Readiness dependency unavailable",
            extra={"dependency": "database", "error_type": type(error).__name__},
        )
    try:
        with redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            if not client.ping():
                raise redis.ConnectionError("Redis ping was not acknowledged")
        checks["redis"] = "ok"
    except Exception as error:
        checks["redis"] = "error"
        logger.warning(
            "Readiness dependency unavailable",
            extra={"dependency": "redis", "error_type": type(error).__name__},
        )
    result = (
        {"status": "ok"}
        if all(value == "ok" for value in checks.values())
        else {"status": "error", "checks": checks}
    )
    response = JsonResponse(result, status=200 if result["status"] == "ok" else 503)
    response["Cache-Control"] = "no-store"
    return response
