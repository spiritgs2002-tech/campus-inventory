import hashlib
import hmac
import json
import time
from django.conf import settings
from django.db import transaction, OperationalError
from django.utils import timezone
from apps.core.models import OperationRequest
from shared.db.runtime import require_runtime
from shared.errors import BusinessError


def digest(payload):
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()
    return hmac.new(settings.AUTH_SIGNING_KEY.encode(), raw, hashlib.sha256).hexdigest()


def validate_key(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise BusinessError(
            "IDEMPOTENCY_KEY_REQUIRED", "请提供不超过128字符的 Idempotency-Key"
        )


@transaction.atomic
def _execute(tenant_id, actor_id, operation, key, payload, callback):
    require_runtime()
    validate_key(key)
    request_digest = digest(payload)
    row, created = OperationRequest.objects.select_for_update().get_or_create(
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation=operation,
        idempotency_key=key,
        defaults={"request_digest": request_digest},
    )
    if not created:
        if not hmac.compare_digest(row.request_digest, request_digest):
            raise BusinessError("IDEMPOTENCY_CONFLICT", "该幂等键已用于不同请求", 409)
        if row.response is None:
            error = BusinessError(
                "ALREADY_PROCESSED", "该请求已处理，响应已过保留期", 409
            )
            error.business_refs = row.business_refs
            raise error
        return row.response, True
    result = callback()
    row.response = result
    row.business_refs = result.get("business_refs", [])
    row.completed_at = timezone.now()
    row.save(update_fields=["response", "business_refs", "completed_at"])
    return result, False


def execute(tenant_id, actor_id, operation, key, payload, callback):
    for attempt in range(3):
        try:
            return _execute(tenant_id, actor_id, operation, key, payload, callback)
        except OperationalError as error:
            code = getattr(error.__cause__, "sqlstate", None)
            if code not in {"40P01", "55P03"}:
                raise
            if attempt == 2:
                raise BusinessError(
                    "RETRYABLE_LOCK_CONFLICT", "操作繁忙，请使用原幂等键重试", 409
                ) from None
            time.sleep(0.02 * (2**attempt))
