"""Scoped maintenance under a configured operations actor, never a public API."""

from datetime import timedelta
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone
from apps.core.auth_service import lock_family
from apps.core.models import AuditLog, AuthSession, OperationRequest, Tenant, User
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.idempotency.service import execute


def scope(tenant_id):
    require_runtime()
    if type(tenant_id) is not int or tenant_id not in settings.MAINTENANCE_ACTORS:
        raise BusinessError("MAINTENANCE_SCOPE_REQUIRED", "租户未配置受控运维身份", 403)
    actor = settings.MAINTENANCE_ACTORS[tenant_id]
    if not User.objects.filter(
        tenant_id=tenant_id,
        pk=actor,
        active=True,
        role="admin",
        must_change_password=False,
    ).exists():
        raise BusinessError(
            "INVALID_MAINTENANCE_ACTOR", "运维身份须为已改密且启用的本校管理员", 403
        )
    return actor


def locks(tenant_id, actor):
    Tenant.objects.select_for_update(no_key=True).get(pk=tenant_id)
    if (
        not User.objects.select_for_update(no_key=True)
        .filter(
            tenant_id=tenant_id,
            pk=actor,
            active=True,
            role="admin",
            must_change_password=False,
        )
        .first()
    ):
        raise BusinessError("INVALID_MAINTENANCE_ACTOR", "运维审计身份已失效", 403)


def audit(tenant_id, actor, action, changes, object_id=None):
    AuditLog.objects.create(
        tenant_id=tenant_id,
        user_id=actor,
        action=action,
        object_type="maintenance",
        object_id=object_id,
        changes=changes,
        reason="受控周期维护",
    )


def command(tenant_id, operation, key, payload, callback, *, dry_run=False):
    actor = scope(tenant_id)

    def work():
        locks(tenant_id, actor)
        return callback(actor)

    if dry_run:
        with transaction.atomic():
            return work(), False
    return execute(tenant_id, actor, operation, key, payload, work)


def limit(value):
    if type(value) is not int or not 1 <= value <= 1000:
        raise BusinessError("INVALID_BATCH_SIZE", "批次上限须为1至1000", 422)
    return value


def cleanup_sessions(tenant_id, key=None, *, batch_size=100, dry_run=False):
    batch_size = limit(batch_size)
    cutoff = timezone.now() - timedelta(days=30)

    def clean(actor):
        candidates = list(
            AuthSession.objects.filter(tenant_id=tenant_id)
            .values("family_id")
            .annotate(last_expiry=Max("expires_at"))
            .filter(last_expiry__lt=cutoff)
            .order_by("family_id")[:batch_size]
        )
        families, skipped, deleted = [], [], 0
        for row in candidates:
            family = row["family_id"]
            lock_family(tenant_id, str(family))
            sessions = list(
                AuthSession.objects.filter(tenant_id=tenant_id, family_id=family)
                .order_by("id")
                .select_for_update()
            )
            if not sessions or any(s.expires_at >= cutoff for s in sessions):
                continue
            ids = [s.pk for s in sessions]
            # An abnormal cross-family parent link must never expand deletion.
            if (
                AuthSession.objects.filter(
                    tenant_id=tenant_id, parent_session_id__in=ids
                )
                .exclude(family_id=family)
                .exists()
            ):
                skipped.append(str(family))
                continue
            if not dry_run:
                AuthSession.objects.filter(tenant_id=tenant_id, pk__in=ids).update(
                    parent_session_id=None
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM auth_sessions WHERE tenant_id=%s AND id=ANY(%s)",
                        [tenant_id, ids],
                    )
                    deleted += cursor.rowcount
            else:
                deleted += len(ids)
            families.append(str(family))
        result = {
            "tenant_id": tenant_id,
            "dry_run": dry_run,
            "families": families,
            "sessions": deleted,
            "skipped_cross_family": skipped,
            "grace_days": 30,
            "business_refs": [{"type": "session_family", "id": f} for f in families],
        }
        if not dry_run:
            audit(tenant_id, actor, "maintenance.sessions", result)
        return result

    return command(
        tenant_id,
        "maintenance.sessions",
        key,
        {"batch_size": batch_size},
        clean,
        dry_run=dry_run,
    )


def cleanup_responses(tenant_id, key=None, *, batch_size=100, dry_run=False):
    batch_size = limit(batch_size)
    cutoff = timezone.now() - timedelta(days=90)

    def clean(actor):
        rows = list(
            OperationRequest.objects.filter(
                tenant_id=tenant_id, completed_at__lt=cutoff, response__isnull=False
            )
            .order_by("id")
            .select_for_update(skip_locked=True)[:batch_size]
        )
        ids = [row.pk for row in rows]
        if ids and not dry_run:
            OperationRequest.objects.filter(tenant_id=tenant_id, pk__in=ids).update(
                response=None
            )
        result = {
            "tenant_id": tenant_id,
            "dry_run": dry_run,
            "cleared": len(ids),
            "business_refs": [{"type": "operation_request", "id": pk} for pk in ids],
        }
        if not dry_run:
            audit(tenant_id, actor, "maintenance.responses", result)
        return result

    return command(
        tenant_id,
        "maintenance.responses",
        key,
        {"batch_size": batch_size},
        clean,
        dry_run=dry_run,
    )
