from datetime import timedelta
from io import StringIO
import hashlib
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4
import boto3
from botocore.stub import Stubber
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from apps.attachments import cleanup, storage
from apps.attachments.content import Evidence
from apps.attachments.models import Attachment, ScrapAttachment
from apps.core.auth_views import error_response
from apps.core.models import AuthSession, OperationRequest, AuditLog, User
from apps.reconcile import maintenance as ops
from apps.reconcile.tasks import cleanup as cleanup_task
from shared.errors import BusinessError
from shared.idempotency.service import execute
from .order_fixtures import OrderFixtureMixin


class MaintenanceTests(OrderFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        root = tempfile.TemporaryDirectory(prefix="p7-evidence-", dir="/tmp")
        self.addCleanup(root.cleanup)
        config = override_settings(
            MAINTENANCE_ACTORS={self.tenant.pk: self.admin.pk},
            ATTACHMENT_CLEANUP_ENABLED=True,
            DEBUG=True,
            ATTACHMENT_STORAGE_BACKEND="filesystem",
            ATTACHMENT_STORAGE_ROOT=root.name,
        )
        config.enable()
        self.addCleanup(config.disable)
        self.store = storage.backend()

    def session(
        self, days=-31, family=None, parent=None, tenant=None, user=None, revoked=False
    ):
        return AuthSession.objects.create(
            tenant_id=(tenant or self.tenant).pk,
            user_id=(user or self.admin).pk,
            family_id=family or uuid4(),
            refresh_digest=uuid4().hex,
            parent_session_id=parent,
            expires_at=timezone.now() + timedelta(days=days),
            revoked_at=timezone.now() if revoked else None,
            revoked_reason="logout" if revoked else None,
        )

    def evidence(self, days=-1, hold=False, tenant=None, user=None):
        tenant, user = tenant or self.tenant, user or self.admin
        key = f"tenants/{tenant.pk}/evidence/{uuid4().hex * 2}.png"
        data = b"fixture-bytes"
        self.store.put(
            tenant.pk,
            key,
            Evidence(data, hashlib.sha256(data).hexdigest(), "image/png", "png"),
        )
        return Attachment.objects.create(
            tenant_id=tenant.pk,
            uploaded_by=user.pk,
            object_key=key,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            content_type="image/png",
            retention_until=timezone.now() + timedelta(days=days),
            legal_hold=hold,
        )

    def test_whole_family_cleanup_replays_and_keeps_other_tenants(self):
        parent = self.session()
        child = self.session(family=parent.family_id, parent=parent.pk, revoked=True)
        foreign = self.session(tenant=self.other_tenant, user=self.other)
        first, replay = ops.cleanup_sessions(self.tenant.pk, "gc")
        second, replay2 = ops.cleanup_sessions(self.tenant.pk, "gc")
        self.assertEqual(first, second)
        self.assertFalse(replay)
        self.assertTrue(replay2)
        self.assertEqual(first["sessions"], 2)
        self.assertFalse(
            AuthSession.objects.filter(pk__in=[parent.pk, child.pk]).exists()
        )
        self.assertTrue(AuthSession.objects.filter(pk=foreign.pk).exists())
        self.assertEqual(
            AuditLog.objects.filter(action="maintenance.sessions").count(), 1
        )

    def test_active_successor_and_recent_expired_and_revoked_are_retained(self):
        parent = self.session()
        self.session(family=parent.family_id, parent=parent.pk, days=1)
        recent = self.session(days=-2)
        revoked = self.session(days=1, revoked=True)
        result, _ = ops.cleanup_sessions(self.tenant.pk, "safe")
        self.assertEqual(result["sessions"], 0)
        self.assertEqual(
            AuthSession.objects.filter(
                pk__in=[parent.pk, recent.pk, revoked.pk]
            ).count(),
            3,
        )

    def test_cross_family_parent_link_is_skipped(self):
        parent = self.session()
        child = self.session(days=1, parent=parent.pk)
        result, _ = ops.cleanup_sessions(self.tenant.pk, "abnormal")
        self.assertIn(str(parent.family_id), result["skipped_cross_family"])
        self.assertTrue(AuthSession.objects.filter(pk=child.pk).exists())

    def test_dry_run_is_read_only_and_cli_alias_works(self):
        obj = self.session()
        output = StringIO()
        call_command(
            "cleanup-sessions", tenant_id=self.tenant.pk, dry_run=True, stdout=output
        )
        self.assertIn('"sessions": 1', output.getvalue())
        self.assertTrue(AuthSession.objects.filter(pk=obj.pk).exists())
        self.assertFalse(OperationRequest.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_unconfigured_scope_and_invalid_actor_and_batch_refused(self):
        with self.assertRaisesMessage(BusinessError, "租户未配置"):
            ops.cleanup_sessions(self.other_tenant.pk, "x")
        with override_settings(MAINTENANCE_ACTORS={self.tenant.pk: self.user.pk}):
            with self.assertRaisesMessage(BusinessError, "管理员"):
                ops.cleanup_sessions(self.tenant.pk, "x")
        User.objects.filter(pk=self.admin.pk).update(must_change_password=True)
        with self.assertRaisesMessage(BusinessError, "已改密"):
            ops.cleanup_sessions(self.tenant.pk, "x")
        for size in [True, 0, 1001, "1"]:
            with self.assertRaises(BusinessError):
                ops.cleanup_sessions(self.tenant.pk, "x", batch_size=size)

    def test_missing_key_and_payload_conflict_do_not_mutate(self):
        obj = self.session()
        with self.assertRaises(BusinessError):
            ops.cleanup_sessions(self.tenant.pk)
        self.assertTrue(AuthSession.objects.filter(pk=obj.pk).exists())
        ops.cleanup_sessions(self.tenant.pk, "x", batch_size=1)
        with self.assertRaisesMessage(BusinessError, "不同请求"):
            ops.cleanup_sessions(self.tenant.pk, "x", batch_size=2)

    def test_disabled_task_has_no_effect(self):
        obj = self.session()
        with override_settings(MAINTENANCE_ENABLED=False):
            result = cleanup_task.apply(args=[self.tenant.pk, "sessions"]).get()
        self.assertEqual(result["error"], "MAINTENANCE_DISABLED")
        self.assertTrue(AuthSession.objects.filter(pk=obj.pk).exists())

    def old_operation(self, days=91):
        return OperationRequest.objects.create(
            tenant_id=self.tenant.pk,
            actor_id=self.admin.pk,
            operation="old",
            idempotency_key=uuid4().hex,
            request_digest="digest",
            response={"record": {"id": 123}},
            business_refs=[{"type": "fixture", "id": 123}],
            completed_at=timezone.now() - timedelta(days=days),
        )

    def test_response_cleanup_preserves_identity_refs_and_replay_refusal(self):
        old, recent = self.old_operation(), self.old_operation(89)
        result, _ = ops.cleanup_responses(self.tenant.pk, "responses")
        self.assertEqual(result["cleared"], 1)
        old.refresh_from_db()
        recent.refresh_from_db()
        self.assertIsNone(old.response)
        self.assertIsNotNone(recent.response)
        self.assertEqual(old.business_refs, [{"type": "fixture", "id": 123}])
        with patch("shared.idempotency.service.digest", return_value="digest"):
            try:
                execute(
                    self.tenant.pk,
                    self.admin.pk,
                    "old",
                    old.idempotency_key,
                    {},
                    lambda: self.fail("rerun"),
                )
            except BusinessError as error:
                self.assertEqual(error.code, "ALREADY_PROCESSED")
                self.assertEqual(error.business_refs, old.business_refs)
                self.assertIn(b"business_refs", error_response(error).content)
            else:
                self.fail("cleared identity was rerun")

    def test_response_cleanup_audit_failure_rolls_back(self):
        old = self.old_operation()
        with patch.object(ops, "audit", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                ops.cleanup_responses(self.tenant.pk, "rollback")
        old.refresh_from_db()
        self.assertIsNotNone(old.response)
        self.assertFalse(
            OperationRequest.objects.filter(idempotency_key="rollback").exists()
        )

    def test_evidence_plan_excludes_held_future_foreign_and_completed(self):
        expired, held, future, foreign = (
            self.evidence(),
            self.evidence(hold=True),
            self.evidence(days=1),
            self.evidence(tenant=self.other_tenant, user=self.other),
        )
        self.assertEqual(
            cleanup._plan(self.tenant.pk, "p", 100)["attachment_ids"], [expired.pk]
        )
        cleanup._mark(self.tenant.pk, expired.pk)
        cleanup._delete(self.tenant.pk, expired.pk, self.store)
        self.assertEqual(list(cleanup._pending(self.tenant.pk)), [])
        for obj in [held, future, foreign]:
            self.assertIsNotNone(self.store.stat(obj.tenant_id, obj.object_key))

    def test_mark_blocks_download_and_retains_association_and_metadata(self):
        obj = self.evidence()
        doc = self.create(
            "scrap",
            "stock_damage",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "quality": "available",
                }
            ],
            reason="proof",
        )
        ScrapAttachment.objects.create(
            tenant_id=self.tenant.pk, scrap_id=doc["id"], attachment_id=obj.pk
        )
        cleanup._mark(self.tenant.pk, obj.pk)
        self.assertEqual(
            self.request("get", f"/api/v1/attachments/{obj.pk}/download/").status_code,
            404,
        )
        cleanup._delete(self.tenant.pk, obj.pk, self.store)
        self.assertTrue(
            Attachment.objects.filter(pk=obj.pk, deleted_at__isnull=False).exists()
        )
        self.assertTrue(ScrapAttachment.objects.filter(attachment_id=obj.pk).exists())
        self.assertIsNone(self.store.stat(self.tenant.pk, obj.object_key))

    def test_failed_delete_preserves_tombstone_then_retries_once(self):
        obj = self.evidence()
        cleanup._mark(self.tenant.pk, obj.pk)
        with patch.object(
            self.store,
            "delete",
            side_effect=BusinessError("STORAGE_UNAVAILABLE", "failure", 503),
        ):
            with self.assertRaises(BusinessError):
                cleanup._delete(self.tenant.pk, obj.pk, self.store)
        obj.refresh_from_db()
        self.assertIsNotNone(obj.deleted_at)
        self.assertFalse(
            OperationRequest.objects.filter(
                operation=f"maintenance.evidence.delete.{obj.pk}"
            ).exists()
        )
        cleanup._delete(self.tenant.pk, obj.pk, self.store)
        cleanup._delete(self.tenant.pk, obj.pk, self.store)
        self.assertEqual(
            AuditLog.objects.filter(action="maintenance.evidence.deleted").count(), 1
        )

    def test_physical_delete_then_db_failure_recovers_missing_object(self):
        obj = self.evidence()
        cleanup._mark(self.tenant.pk, obj.pk)
        with patch.object(
            ops, "audit", side_effect=RuntimeError("post-delete DB failure")
        ):
            with self.assertRaises(RuntimeError):
                cleanup._delete(self.tenant.pk, obj.pk, self.store)
        self.assertIsNone(self.store.stat(self.tenant.pk, obj.object_key))
        self.assertEqual(
            cleanup._delete(self.tenant.pk, obj.pk, self.store)["status"], "deleted"
        )

    def test_changed_policy_after_plan_is_rechecked_without_permanent_skip(self):
        obj = self.evidence()
        cleanup._plan(self.tenant.pk, "plan", 100)
        Attachment.objects.filter(pk=obj.pk).update(legal_hold=True)
        with self.assertRaises(BusinessError):
            cleanup._mark(self.tenant.pk, obj.pk)
        self.assertFalse(
            OperationRequest.objects.filter(
                operation=f"maintenance.evidence.mark.{obj.pk}"
            ).exists()
        )
        self.assertIsNotNone(self.store.stat(self.tenant.pk, obj.object_key))

    def test_evidence_cleanup_requires_explicit_enable_and_top_level(self):
        with override_settings(ATTACHMENT_CLEANUP_ENABLED=False):
            with self.assertRaisesMessage(BusinessError, "尚未显式启用"):
                cleanup.cleanup_evidence(self.tenant.pk, "x")
        with self.assertRaisesMessage(BusinessError, "不能嵌套"):
            cleanup.cleanup_evidence(self.tenant.pk, "x")

    def test_forced_password_change_blocks_attachment_paths(self):
        from shared.auth.backends import authorize_claims
        from shared.auth.tokens import access_token

        token = access_token(authorize_claims(self.claims[self.admin.pk]))
        User.objects.filter(pk=self.admin.pk).update(must_change_password=True)
        for path in ["/api/v1/attachments/", "/api/v1/stock/"]:
            result = self.client.get(path, HTTP_AUTHORIZATION="Bearer " + token)
            self.assertEqual(result.status_code, 403)


class StorageCleanupTests(SimpleTestCase):
    def test_local_symlink_parent_cannot_escape_tenant_scope(self):
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as root,
            override_settings(DEBUG=True, ATTACHMENT_STORAGE_ROOT=root),
        ):
            store = storage.LocalStore()
            target = Path(root) / "tenants/2/evidence"
            target.mkdir(parents=True)
            (Path(root) / "tenants/1").symlink_to(
                target.parent, target_is_directory=True
            )
            key = "tenants/1/evidence/" + "a" * 64 + ".png"
            for operation in [
                lambda: store.stat(1, key),
                lambda: store.scan(1),
                lambda: store.delete(1, key),
            ]:
                with self.assertRaisesMessage(BusinessError, "符号链接"):
                    operation()

    def test_s3_versioned_bucket_is_rejected(self):
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="fixture",
            aws_secret_access_key="fixture",
        )
        for state in ["Enabled", "Suspended"]:
            with Stubber(client) as stub:
                stub.add_response(
                    "get_bucket_versioning", {"Status": state}, {"Bucket": "private"}
                )
                with self.assertRaisesMessage(BusinessError, "历史版本策略"):
                    storage.S3Store(client, "private").cleanup_ready()

    def test_s3_delete_is_conditional_and_changed_etag_is_rejected(self):
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="fixture",
            aws_secret_access_key="fixture",
        )
        key = "tenants/1/evidence/" + "a" * 64 + ".png"
        with Stubber(client) as stub:
            stub.add_client_error(
                "delete_object",
                "PreconditionFailed",
                http_status_code=412,
                expected_params={"Bucket": "private", "Key": key, "IfMatch": '"old"'},
            )
            with self.assertRaisesMessage(BusinessError, "对象已变化"):
                storage.S3Store(client, "private").delete(1, key, {"etag": '"old"'})

    def test_local_scan_is_scoped_paged_and_skips_symlinks(self):
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as root,
            override_settings(DEBUG=True, ATTACHMENT_STORAGE_ROOT=root),
        ):
            store = storage.LocalStore()
            keys = [
                f"tenants/{tid}/evidence/{char * 64}.png"
                for tid, char in [(1, "a"), (1, "b"), (2, "c")]
            ]
            for key in keys:
                path = Path(root) / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            (Path(root) / "tenants/1/evidence" / ("d" * 64 + ".png")).symlink_to(
                Path(root) / keys[2]
            )
            first, cursor = store.scan(1, limit=1)
            second, end = store.scan(1, cursor, 1)
            self.assertEqual([e["key"] for e in first + second], keys[:2])
            self.assertIsNone(end)
            observed = store.stat(1, keys[0])
            os.utime(Path(root) / keys[0], (1, 1))
            with self.assertRaises(BusinessError):
                store.delete(1, keys[0], observed)
