"""Committed tombstones plus retryable physical deletion, retaining metadata."""

from datetime import datetime, timedelta
import hashlib
from django.conf import settings
from django.db import connection
from django.db.models import CharField, Exists, OuterRef, Value
from django.db.models.functions import Cast, Concat
from django.utils import timezone
from apps.core.models import OperationRequest
from apps.reconcile import maintenance as ops
from shared.errors import BusinessError
from .models import Attachment
from . import storage


def _guard(tenant_id, *, top_level=False):
    ops.scope(tenant_id)
    if not settings.ATTACHMENT_CLEANUP_ENABLED:
        raise BusinessError(
            "EVIDENCE_CLEANUP_DISABLED", "学校证据清理策略尚未显式启用", 403
        )
    if top_level and (connection.in_atomic_block or not connection.get_autocommit()):
        raise BusinessError(
            "MAINTENANCE_REQUIRES_TOP_LEVEL",
            "对象清理必须独立执行，不能嵌套业务事务",
            409,
        )


def _pending(tenant_id):
    complete = OperationRequest.objects.filter(
        tenant_id=tenant_id,
        operation=Concat(
            Value("maintenance.evidence.delete."), Cast(OuterRef("pk"), CharField())
        ),
    )
    return (
        Attachment.objects.filter(
            tenant_id=tenant_id, legal_hold=False, retention_until__lte=timezone.now()
        )
        .annotate(physically_complete=Exists(complete))
        .filter(physically_complete=False)
    )


def _plan(tenant_id, key, batch_size, *, dry_run=False):
    def plan(actor):
        ids = list(
            _pending(tenant_id).order_by("id").values_list("id", flat=True)[:batch_size]
        )
        result = {
            "attachment_ids": ids,
            "dry_run": dry_run,
            "business_refs": [{"type": "attachment", "id": pk} for pk in ids],
        }
        if not dry_run:
            ops.audit(tenant_id, actor, "maintenance.evidence.plan", result)
        return result

    return ops.command(
        tenant_id,
        "maintenance.evidence.plan",
        key,
        {"batch_size": batch_size},
        plan,
        dry_run=dry_run,
    )[0]


def _mark(tenant_id, pk):
    def mark(actor):
        obj = Attachment.objects.select_for_update(no_key=True).get(
            tenant_id=tenant_id, pk=pk
        )
        if obj.legal_hold or obj.retention_until > timezone.now():
            raise BusinessError("EVIDENCE_PROTECTED", "保全或保留策略已变化", 409)
        storage.validate_key(tenant_id, obj.object_key)
        if obj.deleted_at is None:
            obj.deleted_at = timezone.now()
            obj.save(update_fields=["deleted_at"])
            ops.audit(
                tenant_id, actor, "maintenance.evidence.mark", {"attachment_id": pk}, pk
            )
        return {"status": "marked", "business_refs": [{"type": "attachment", "id": pk}]}

    return ops.command(
        tenant_id, f"maintenance.evidence.mark.{pk}", f"attachment:{pk}", {}, mark
    )[0]


def _delete(tenant_id, pk, store):
    def remove(actor):
        obj = Attachment.objects.select_for_update(no_key=True).get(
            tenant_id=tenant_id, pk=pk
        )
        if (
            obj.deleted_at is None
            or obj.legal_hold
            or obj.retention_until > timezone.now()
        ):
            raise BusinessError(
                "EVIDENCE_PROTECTED", "证据未标记或保全/保留策略已变化", 409
            )
        observed = store.stat(tenant_id, obj.object_key)
        removed = store.delete(tenant_id, obj.object_key, observed)
        if store.stat(tenant_id, obj.object_key) is not None:
            raise BusinessError(
                "STORAGE_UNAVAILABLE", "删除结果未确认，保留重试状态", 503
            )
        result = {
            "status": "deleted",
            "removed": removed,
            "business_refs": [{"type": "attachment", "id": pk}],
        }
        ops.audit(tenant_id, actor, "maintenance.evidence.deleted", result, pk)
        return result

    return ops.command(
        tenant_id, f"maintenance.evidence.delete.{pk}", f"attachment:{pk}", {}, remove
    )[0]


def cleanup_evidence(tenant_id, key=None, *, batch_size=100, dry_run=False):
    _guard(tenant_id, top_level=True)
    batch_size = ops.limit(batch_size)
    store = storage.backend()
    store.cleanup_ready()
    plan = _plan(tenant_id, key, batch_size, dry_run=dry_run)
    if dry_run:
        return plan
    statuses = []
    for pk in plan["attachment_ids"]:
        try:
            obj = Attachment.objects.get(tenant_id=tenant_id, pk=pk)
            if obj.deleted_at is None:
                status = _mark(tenant_id, pk)["status"]
                if status != "marked":
                    statuses.append({"id": pk, "status": status})
                    continue
            # The mark transaction committed before any physical deletion.
            result = _delete(tenant_id, pk, store)
            statuses.append({"id": pk, "status": result["status"]})
        except BusinessError as error:
            status = {
                "EVIDENCE_PROTECTED": "protected",
                "ALREADY_PROCESSED": "already_processed",
            }.get(error.code, "retry")
            statuses.append({"id": pk, "status": status, "error": error.code})
    return {
        "tenant_id": tenant_id,
        "items": statuses,
        "ok": not any(r["status"] == "retry" for r in statuses),
    }


def _orphan_token(entry):
    return hashlib.sha256(
        f"{entry['key']}:{entry['modified']}:{entry['etag']}".encode()
    ).hexdigest()


def cleanup_orphans(tenant_id, key=None, *, batch_size=100, dry_run=False):
    _guard(tenant_id, top_level=True)
    batch_size = ops.limit(batch_size)
    store = storage.backend()
    store.cleanup_ready()
    cutoff = timezone.now() - timedelta(hours=max(24, settings.ORPHAN_GRACE_HOURS))

    def plan(actor):
        previous = (
            OperationRequest.objects.filter(
                tenant_id=tenant_id,
                operation="maintenance.orphans.plan",
                response__isnull=False,
            )
            .order_by("-completed_at", "-id")
            .first()
        )
        cursor = previous.response.get("next_cursor") if previous else None
        entries, next_cursor = store.scan(tenant_id, cursor, batch_size)
        candidates = [
            e
            for e in entries
            if datetime.fromisoformat(e["modified"]) < cutoff
            and not Attachment.objects.filter(
                tenant_id=tenant_id, object_key=e["key"]
            ).exists()
        ]
        result = {
            "objects": candidates,
            "next_cursor": next_cursor,
            "business_refs": [
                {"type": "orphan_object", "id": _orphan_token(e)} for e in candidates
            ],
        }
        if not dry_run:
            ops.audit(
                tenant_id, actor, "maintenance.orphans.plan", {"count": len(candidates)}
            )
        return result

    planned = ops.command(
        tenant_id,
        "maintenance.orphans.plan",
        key,
        {"batch_size": batch_size},
        plan,
        dry_run=dry_run,
    )[0]
    if dry_run:
        return {
            "dry_run": True,
            "objects": [e["key"] for e in planned["objects"]],
            "next_cursor": planned["next_cursor"],
        }
    statuses = []
    for entry in planned["objects"]:
        token = _orphan_token(entry)

        def remove(actor):
            storage.validate_key(tenant_id, entry["key"])
            if Attachment.objects.filter(
                tenant_id=tenant_id, object_key=entry["key"]
            ).exists():
                return {"status": "registered", "business_refs": []}
            current = store.stat(tenant_id, entry["key"])
            if current and (current["modified"], current["etag"]) != (
                entry["modified"],
                entry["etag"],
            ):
                return {"status": "changed", "business_refs": []}
            store.delete(tenant_id, entry["key"], current)
            if store.stat(tenant_id, entry["key"]) is not None:
                raise BusinessError("STORAGE_UNAVAILABLE", "未确认孤儿对象删除", 503)
            result = {
                "status": "deleted",
                "business_refs": [{"type": "orphan_object", "id": token}],
            }
            ops.audit(
                tenant_id, actor, "maintenance.orphans.deleted", {"object_hash": token}
            )
            return result

        try:
            result = ops.command(
                tenant_id, f"maintenance.orphans.delete.{token}", token, {}, remove
            )[0]
            statuses.append({"object_hash": token, "status": result["status"]})
        except BusinessError as error:
            statuses.append(
                {"object_hash": token, "status": "retry", "error": error.code}
            )
    return {
        "tenant_id": tenant_id,
        "items": statuses,
        "next_cursor": planned["next_cursor"],
        "ok": not any(r["status"] == "retry" for r in statuses),
    }
