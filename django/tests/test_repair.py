import hashlib
from importlib import import_module
from io import BytesIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, SimpleTestCase, TestCase, override_settings
from PIL import Image

from apps.attachments import services as attachments
from apps.attachments.models import Attachment
from apps.core.models import AuditLog, AuthSession, OperationRequest, User
from apps.inventory.models import AssetCard, AssetEvent
from apps.inventory.qr import generate_item_tokens
from apps.repair import services
from apps.repair.models import RepairTicket
from shared.auth.backends import authorize_claims
from shared.auth.tokens import access_token
from shared.errors import BusinessError
from .order_fixtures import OrderFixtureMixin


def photo(color="red"):
    stream = BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format="PNG")
    return SimpleUploadedFile("fault.png", stream.getvalue(), "image/png")


class RepairMigrationTests(SimpleTestCase):
    def test_incremental_chapter_and_hash_match_authoritative_snapshot(self):
        current = (settings.SQL_ROOT / "schema-v7-current.sql").read_text()
        start = current.index("-- P8C-REPAIR-BEGIN")
        end = current.index("-- P8C-REPAIR-END") + len("-- P8C-REPAIR-END\n")
        delta = (settings.SQL_ROOT / "p8c-repair.sql").read_bytes()
        self.assertIn(current[start:end].encode(), delta)
        from django.db import migrations

        operation = import_module(
            "apps.repair.migrations.0001_initial"
        ).Migration.operations[0]
        self.assertIsInstance(operation, migrations.RunSQL)
        self.assertEqual(operation.sha256, hashlib.sha256(delta).hexdigest())
        self.assertFalse(RepairTicket._meta.managed)


class RepairTests(OrderFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory(prefix="repair-evidence-", dir="/tmp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        config = override_settings(
            DEBUG=True,
            ATTACHMENT_STORAGE_BACKEND="filesystem",
            ATTACHMENT_STORAGE_ROOT=directory.name,
            ATTACHMENT_RETENTION_DAYS="30",
        )
        config.enable()
        self.addCleanup(config.disable)

    def principal(self, user=None):
        return authorize_claims(self.claims[(user or self.user).pk])

    def payload(self, **changes):
        return {
            "target_type": "item",
            "target_id": self.item.pk,
            "description": "复印纸包装受损",
            **changes,
        }

    def ticket(self, user=None, **changes):
        result, _ = services.repair_submit(
            self.principal(user), self.payload(**changes), uuid4().hex
        )
        return result["document"]

    def transition(self, pk, action, key=None, **params):
        return services.repair_transition(
            self.principal(self.admin), pk, action, key or uuid4().hex, **params
        )

    def token(self):
        generate_item_tokens(self.principal(self.admin), uuid4().hex)
        self.item.refresh_from_db()
        return self.item.qr_token

    def anon(self, **changes):
        return self.payload(
            **{
                "token": self.token(),
                "name": "免密教师",
                "employee_no": "REPAIR-01",
                **changes,
            }
        )

    def upload(self, user=None):
        result, _ = attachments.upload(self.principal(user), uuid4().hex, photo())
        return result["record"]["id"]

    def test_api_create_with_uploaded_photo_and_trusted_identity(self):
        uploaded = self.client.post(
            "/api/v1/attachments/",
            {"file": photo()},
            HTTP_AUTHORIZATION="Bearer " + access_token(self.principal()),
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.content)
        attachment_id = uploaded.json()["record"]["id"]
        response = self.request(
            "post",
            "/api/v1/repair/",
            self.payload(
                photo_attachment_id=attachment_id, name="冒名", employee_no="other"
            ),
            user=self.user,
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["document"]
        self.assertEqual(
            (data["status"], data["reporter_name"], data["reporter_no"]),
            ("pending", self.user.name, self.user.username),
        )
        self.assertEqual(data["photo_attachment_id"], attachment_id)
        self.assertIn("no-store", response["Cache-Control"])

    def test_replay_and_full_payload_conflicts(self):
        key = uuid4().hex
        result, replay = services.repair_submit(self.principal(), self.payload(), key)
        self.assertFalse(replay)
        again, replay = services.repair_submit(self.principal(), self.payload(), key)
        self.assertTrue(replay)
        self.assertEqual(again, result)
        for changes in (
            {"description": "另一故障"},
            {"priority": "high"},
            {"photo_attachment_id": 999},
        ):
            with self.assertRaisesMessage(BusinessError, "该幂等键已用于不同请求"):
                services.repair_submit(self.principal(), self.payload(**changes), key)
        self.assertEqual(RepairTicket.objects.count(), 1)

    def test_required_and_strict_fields(self):
        for changes in (
            {"description": " "},
            {"description": "x" * 4001},
            {"target_type": "other"},
            {"target_id": 0},
            {"target_id": None},
            {"priority": "urgent"},
            {"reporter_user_id": self.admin.pk},
            {"tenant_id": self.other_tenant.pk},
            {"target_id": True},
            {"description": "\ud800"},
        ):
            with self.subTest(changes=repr(changes)), self.assertRaises(BusinessError):
                services.repair_submit(
                    self.principal(), self.payload(**changes), uuid4().hex
                )

    def test_location_without_id_is_supported(self):
        row = self.ticket(target_type="location", target_id=None)
        self.assertIsNone(row["target_id"])

    def test_foreign_school_target_rejected(self):
        with self.assertRaises(BusinessError):
            self.ticket(target_type="location", target_id=self.other_location.pk)

    def test_photo_owner_not_just_same_school(self):
        uploaded = self.upload(self.admin)
        with self.assertRaises(BusinessError):
            self.ticket(photo_attachment_id=uploaded)
        self.assertFalse(RepairTicket.objects.exists())

    def test_foreign_school_photo_rejected(self):
        uploaded = self.upload(self.other)
        with self.assertRaises(BusinessError):
            self.ticket(photo_attachment_id=uploaded)

    def test_deleted_photo_rejected(self):
        from django.utils import timezone

        uploaded = self.upload()
        Attachment.objects.filter(pk=uploaded).update(deleted_at=timezone.now())
        with self.assertRaises(BusinessError):
            self.ticket(photo_attachment_id=uploaded)

    def test_non_image_attachment_rejected(self):
        from pypdf import PdfWriter

        stream = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)
        result, _ = attachments.upload(
            self.principal(),
            uuid4().hex,
            SimpleUploadedFile("fault.pdf", stream.getvalue(), "application/pdf"),
        )
        with self.assertRaises(BusinessError):
            self.ticket(photo_attachment_id=result["record"]["id"])

    def test_anonymous_reuses_identity_without_session_and_replays(self):
        count = AuthSession.objects.count()
        payload, key = self.anon(), uuid4().hex
        first, _ = services.repair_submit(None, payload, key)
        second, replay = services.repair_submit(None, payload, key)
        self.assertTrue(replay)
        self.assertEqual(first, second)
        services.repair_submit(None, payload, uuid4().hex)
        self.assertEqual(
            User.objects.filter(tenant_id=self.tenant.pk, username="REPAIR-01").count(),
            1,
        )
        self.assertEqual(AuthSession.objects.count(), count)

    def test_anonymous_missing_identity_is_422(self):
        for payload in [
            self.payload(),
            self.payload(name="教师"),
            self.payload(employee_no="R1"),
        ]:
            with self.assertRaises(BusinessError) as caught:
                services.repair_submit(None, payload, uuid4().hex)
            self.assertEqual(caught.exception.status, 422)

    def test_anonymous_requires_opaque_token(self):
        with self.assertRaises(BusinessError):
            services.repair_submit(
                None, self.payload(name="教师", employee_no="R1"), uuid4().hex
            )
        self.assertFalse(User.objects.filter(username="R1").exists())

    def test_anonymous_formal_or_disabled_identity_not_reused(self):
        for user in [self.user, self.admin]:
            with self.assertRaises(BusinessError):
                services.repair_submit(
                    None,
                    self.anon(name=user.name, employee_no=user.username),
                    uuid4().hex,
                )

    def test_anonymous_wrong_target_does_not_create_user(self):
        data = self.anon()
        data["target_id"] = self.fixed.pk
        with self.assertRaises(BusinessError):
            services.repair_submit(None, data, uuid4().hex)
        self.assertFalse(User.objects.filter(username="REPAIR-01").exists())

    def test_anonymous_audit_failure_rolls_back_trace_user_and_ticket(self):
        data = self.anon()
        with patch(
            "apps.repair.services.core.audit", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                services.repair_submit(None, data, uuid4().hex)
        self.assertFalse(RepairTicket.objects.exists())
        self.assertFalse(User.objects.filter(username="REPAIR-01").exists())
        self.assertFalse(
            OperationRequest.objects.filter(operation=services.OPERATION).exists()
        )

    def test_anonymous_photo_requires_real_login(self):
        with self.assertRaises(BusinessError) as caught:
            services.repair_submit(None, self.anon(), uuid4().hex, file=photo())
        self.assertEqual(caught.exception.status, 401)

    def test_invalid_bearer_never_downgrades_to_anonymous(self):
        response = self.client.post(
            "/api/v1/repair/",
            json.dumps(self.anon()),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid",
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(RepairTicket.objects.exists())

    def test_complete_state_machine_and_audit(self):
        row = self.ticket()
        for action, status, params in [
            ("dispatch", "dispatched", {"assigned_to": self.user.pk}),
            ("start", "repairing", {}),
            ("done", "done", {}),
        ]:
            result, _ = self.transition(row["id"], action, **params)
            self.assertEqual(result["document"]["status"], status)
        self.assertEqual(
            AuditLog.objects.filter(
                tenant_id=self.tenant.pk, object_type="repair"
            ).count(),
            4,
        )

    def test_state_machine_api_and_html_dispatch(self):
        row = self.ticket()
        url = f"/api/v1/repair/{row['id']}"
        result = self.request("post", url + "/dispatch/", {"assigned_to": self.user.pk})
        self.assertEqual(result.status_code, 200, result.content)
        for action, status in [("start", "repairing"), ("done", "done")]:
            result = self.request("post", url + "/status/", {"action": action})
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(result.json()["document"]["status"], status)
        row = self.ticket()
        response = self.client.post(
            f"/manage/repairs/{row['id']}/dispatch/",
            {"assigned_to": self.user.pk, "idempotency_key": uuid4().hex},
            HTTP_AUTHORIZATION="Bearer " + access_token(self.principal(self.admin)),
        )
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(RepairTicket.objects.get(pk=row["id"]).status, "dispatched")

    def test_no_js_anonymous_form_enforces_csrf_and_replay(self):
        client = Client(enforce_csrf_checks=True)
        token = self.token()
        page = client.get("/s/repair/new/", {"token": token})
        csrf = page.cookies["csrftoken"].value
        data = {
            **self.anon(),
            "csrfmiddlewaretoken": csrf,
            "idempotency_key": uuid4().hex,
        }
        response = client.post("/s/repair/new/", data)
        self.assertContains(response, "报修已登记")
        replay = client.post("/s/repair/new/", data)
        self.assertContains(replay, "未重复建单")
        self.assertEqual(RepairTicket.objects.count(), 1)

    def test_dispatch_replay_and_different_key_conflict(self):
        row, key = self.ticket(), uuid4().hex
        self.transition(row["id"], "dispatch", key, assigned_to=self.user.pk)
        _, replay = self.transition(
            row["id"], "dispatch", key, assigned_to=self.user.pk
        )
        self.assertTrue(replay)
        with self.assertRaises(BusinessError):
            self.transition(row["id"], "dispatch", assigned_to=self.admin.pk)

    def test_illegal_transitions_rejected(self):
        row = self.ticket()
        for action in ["start", "done"]:
            with self.assertRaises(BusinessError) as caught:
                self.transition(row["id"], action)
            self.assertEqual(caught.exception.status, 409)
        self.transition(row["id"], "dispatch", assigned_to=self.user.pk)
        with self.assertRaises(BusinessError):
            self.transition(row["id"], "done")

    def test_cancel_any_nonterminal_and_freeze(self):
        for advance in [0, 1, 2]:
            row = self.ticket()
            if advance:
                self.transition(row["id"], "dispatch", assigned_to=self.user.pk)
            if advance == 2:
                self.transition(row["id"], "start")
            result, _ = self.transition(row["id"], "cancel", reason="重复故障")
            self.assertEqual(result["document"]["status"], "cancelled")
            for action in ["start", "done", "cancel", "dispatch"]:
                with self.assertRaises(BusinessError):
                    self.transition(
                        row["id"],
                        action,
                        assigned_to=self.user.pk if action == "dispatch" else None,
                    )

    def test_done_is_terminal(self):
        row = self.ticket()
        for action in ["dispatch", "start", "done"]:
            self.transition(
                row["id"],
                action,
                **({"assigned_to": self.user.pk} if action == "dispatch" else {}),
            )
        with self.assertRaises(BusinessError):
            self.transition(row["id"], "cancel")

    def test_assignee_must_be_active_and_same_school(self):
        row = self.ticket()
        with self.assertRaises(BusinessError):
            self.transition(row["id"], "dispatch", assigned_to=self.other.pk)
        User.objects.filter(pk=self.approver.pk).update(active=False)
        with self.assertRaises(BusinessError):
            self.transition(row["id"], "dispatch", assigned_to=self.approver.pk)

    def test_teacher_and_anonymous_cannot_change_status(self):
        row = self.ticket()
        for user, status in [(self.user, 403), (None, 401)]:
            with self.assertRaises(BusinessError) as caught:
                services.repair_transition(
                    self.principal(user) if user else None,
                    row["id"],
                    "cancel",
                    uuid4().hex,
                )
            self.assertEqual(caught.exception.status, status)

    def test_owner_detail_and_other_user_403(self):
        row = self.ticket(self.admin)
        self.assertEqual(
            self.request(
                "get", f"/api/v1/repair/{row['id']}/", user=self.user
            ).status_code,
            403,
        )
        self.assertEqual(
            self.request("get", f"/s/repairs/{row['id']}/", user=self.user).status_code,
            403,
        )
        self.assertEqual(
            self.request(
                "get", f"/api/v1/repair/{row['id']}/", user=self.other
            ).status_code,
            403,
        )
        self.assertEqual(
            self.request("get", f"/api/v1/repair/{row['id']}/").status_code, 200
        )

    def test_lists_scope_filter_and_admin_access(self):
        own, other = self.ticket(), self.ticket(self.admin)
        response = self.request("get", "/s/repairs/", user=self.user)
        self.assertContains(response, own["ticket_no"])
        self.assertNotContains(response, other["ticket_no"])
        self.assertEqual(
            self.request("get", "/manage/repairs/", user=self.user).status_code, 403
        )
        self.assertContains(self.request("get", "/manage/repairs/"), other["ticket_no"])
        self.assertNotContains(
            self.request("get", "/s/repairs/", {"status": "done"}, user=self.user),
            own["ticket_no"],
        )

    def test_cross_tenant_transition_denied(self):
        row = self.ticket()
        response = self.request(
            "post",
            f"/api/v1/repair/{row['id']}/status/",
            {"action": "cancel"},
            user=self.other,
        )
        self.assertEqual(response.status_code, 403)

    def test_asset_flow_does_not_change_asset_state_or_events(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        before = AssetEvent.objects.count()
        row = self.ticket(target_type="asset", target_id=card.pk)
        for action in ["dispatch", "start", "done"]:
            self.transition(
                row["id"],
                action,
                **({"assigned_to": self.user.pk} if action == "dispatch" else {}),
            )
        card.refresh_from_db()
        self.assertEqual(card.status, "in_stock")
        self.assertEqual(AssetEvent.objects.count(), before)

    def test_database_polymorphic_target_cannot_reference_foreign_tenant(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RepairTicket.objects.create(
                tenant_id=self.tenant.pk,
                ticket_no="RP-RAW",
                reporter_user_id=self.user.pk,
                reporter_name=self.user.name,
                reporter_no=self.user.username,
                target_type="location",
                target_id=self.other_location.pk,
                description="违规测试",
            )

    def test_csrf_and_content_type(self):
        client = Client(enforce_csrf_checks=True)
        denied = client.post(
            "/api/v1/repair/",
            json.dumps(self.anon()),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
        )
        self.assertEqual(denied.status_code, 403)
        token = access_token(self.principal())
        allowed = client.post(
            "/api/v1/repair/",
            json.dumps(self.payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
        )
        self.assertEqual(allowed.status_code, 201, allowed.content)
        self.assertEqual(self.client.get("/api/v1/repair/").status_code, 405)

    def test_html_photo_upload_replay_and_changed_bytes(self):
        key = uuid4().hex
        headers = {"HTTP_AUTHORIZATION": "Bearer " + access_token(self.principal())}
        data = {**self.payload(), "idempotency_key": key, "photo": photo()}
        first = self.client.post("/s/repair/new/", data, **headers)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(RepairTicket.objects.count(), 1)
        self.assertEqual(Attachment.objects.count(), 1)
        second = self.client.post(
            "/s/repair/new/",
            {**self.payload(), "idempotency_key": key, "photo": photo()},
            **headers,
        )
        self.assertContains(second, "未重复建单")
        conflict = self.client.post(
            "/s/repair/new/",
            {**self.payload(), "idempotency_key": key, "photo": photo("blue")},
            **headers,
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(Attachment.objects.count(), 1)

    def test_upload_failure_and_audit_failure_do_not_commit_ticket(self):
        original = services.core.audit

        def failure(principal, resource, *args, **kwargs):
            if resource == "repair":
                raise RuntimeError("injected after photo registration")
            return original(principal, resource, *args, **kwargs)

        with patch("apps.repair.services.core.audit", side_effect=failure):
            with self.assertRaises(RuntimeError):
                services.repair_submit(
                    self.principal(), self.payload(), uuid4().hex, file=photo()
                )
        self.assertFalse(RepairTicket.objects.exists())
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(
            OperationRequest.objects.filter(
                operation__in=["repair.ticket.create", "attachments.upload"]
            ).exists()
        )

    @override_settings(ATTACHMENT_MAX_BYTES=20)
    def test_repair_upload_guard_rejects_oversized_file(self):
        response = self.client.post(
            "/s/repair/new/",
            {**self.payload(), "idempotency_key": uuid4().hex, "photo": photo()},
            HTTP_AUTHORIZATION="Bearer " + access_token(self.principal()),
        )
        self.assertEqual(response.status_code, 422)
        self.assertFalse(RepairTicket.objects.exists())

    def test_html_prefill_escaping_and_get_is_read_only(self):
        token = self.token()
        response = self.client.get("/s/repair/new/", {"token": token})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RepairTicket.objects.exists())
        row = self.ticket(description='<script>alert("x")</script>')
        response = self.request("get", f"/s/repairs/{row['id']}/", user=self.user)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("x")</script>')
