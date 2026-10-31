from datetime import timedelta
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import uuid4
import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.test import Client
from django.conf import settings
from django.utils import timezone
from PIL import Image
from pypdf import PdfWriter
from apps.attachments import services, storage
from apps.attachments.content import Evidence
from apps.attachments.models import Attachment, ScrapAttachment
from apps.attachments.tasks import verify_integrity
from apps.core.models import AuthSession, OperationRequest
from shared.auth.backends import authorize_claims
from shared.auth.tokens import access_token
from shared.errors import BusinessError
from .order_fixtures import OrderFixtureMixin


def png(color="red"):
    stream = BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format="PNG")
    return stream.getvalue()


class AttachmentTests(OrderFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        root = tempfile.TemporaryDirectory(prefix="evidence-test-", dir="/tmp")
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        config = override_settings(
            DEBUG=True,
            ATTACHMENT_STORAGE_BACKEND="filesystem",
            ATTACHMENT_STORAGE_ROOT=root.name,
            ATTACHMENT_RETENTION_DAYS="30",
        )
        config.enable()
        self.addCleanup(config.disable)

    def principal(self, user=None):
        return authorize_claims(self.claims[(user or self.admin).pk])

    def multipart(self, path, data, user=None, key=None):
        return self.client.post(
            path,
            data,
            HTTP_AUTHORIZATION="Bearer " + access_token(self.principal(user)),
            HTTP_IDEMPOTENCY_KEY=key or str(uuid4()),
        )

    def upload(self, user=None, key=None, data=None):
        return self.multipart(
            "/api/v1/attachments/",
            {"file": SimpleUploadedFile("evidence.png", data or png(), "image/png")},
            user,
            key,
        )

    def attachment(self, user=None):
        response = self.upload(user)
        self.assertEqual(response.status_code, 201, response.content)
        return Attachment.objects.get(pk=response.json()["record"]["id"])

    def scrap(self):
        return self.create(
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
            reason="待附证据",
        )

    def link(self, document, obj, method="post", reason="", user=None, key=None):
        return self.request(
            method,
            f"/api/v1/scrap/{document['id']}/attachments/{obj.pk}/",
            {"reason": reason},
            user,
            key,
        )

    def test_upload_replay_rechecks_bytes_and_keeps_server_owned_metadata(self):
        key = str(uuid4())
        first = self.upload(key=key)
        self.assertEqual(first.status_code, 201, first.content)
        second = self.upload(key=key)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second["Idempotency-Replayed"], "true")
        obj = Attachment.objects.get()
        self.assertTrue(
            obj.object_key.startswith(f"tenants/{self.tenant.pk}/evidence/")
        )
        self.assertEqual(obj.sha256, hashlib.sha256(png()).hexdigest())
        self.assertEqual(obj.size_bytes, len(png()))
        self.assertGreater(obj.retention_until, timezone.now() + timedelta(days=29))
        self.assertNotIn("object_key", first.json()["record"])
        self.assertEqual(self.upload(key=key, data=png("blue")).status_code, 409)
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(len(list(self.root.rglob("*.png"))), 1)

    def test_content_type_extension_digest_and_server_size_validation(self):
        cases = [
            ("a.txt", png(), "text/plain", {}),
            ("a.png", b"", "image/png", {}),
            ("a.png", b"not a png", "image/png", {}),
            ("a.jpg", png(), "image/jpeg", {}),
            ("a.png", png(), "text/plain", {}),
            ("a.pdf", png(), "application/pdf", {}),
            ("a.png", png(), "image/png", {"sha256": "0" * 64}),
            ("a.png", png(), "image/png", {"object_key": "other-tenant/key"}),
        ]
        for name, data, mime, extra in cases:
            with self.subTest(name=name, mime=mime, extra=extra):
                response = self.multipart(
                    "/api/v1/attachments/",
                    {"file": SimpleUploadedFile(name, data, mime), **extra},
                )
                self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_streaming_handler_rejects_oversize_and_multiple_files(self):
        with override_settings(ATTACHMENT_MAX_BYTES=100):
            response = self.upload(data=b"x" * 101)
            self.assertEqual(response.status_code, 422, response.content)
            self.assertEqual(response.json()["error"]["code"], "INVALID_FILE_SIZE")
        response = self.multipart(
            "/api/v1/attachments/",
            {
                "file": [
                    SimpleUploadedFile("1.png", png(), "image/png"),
                    SimpleUploadedFile("2.png", png(), "image/png"),
                ]
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertFalse(Attachment.objects.exists())

    def test_pdf_accepts_passive_and_rejects_active_or_encrypted(self):
        for mode, expected in [("passive", 201), ("active", 422), ("encrypted", 422)]:
            with self.subTest(mode=mode):
                writer = PdfWriter()
                writer.add_blank_page(width=100, height=100)
                if mode == "active":
                    writer.add_js("app.alert('not permitted')")
                if mode == "encrypted":
                    writer.encrypt("synthetic-test-password")
                stream = BytesIO()
                writer.write(stream)
                response = self.multipart(
                    "/api/v1/attachments/",
                    {
                        "file": SimpleUploadedFile(
                            "evidence.pdf", stream.getvalue(), "application/pdf"
                        )
                    },
                )
                self.assertEqual(response.status_code, expected, response.content)

    def test_retention_and_storage_configuration_fail_closed(self):
        for setting in ["", "0", "not-a-number"]:
            with override_settings(ATTACHMENT_RETENTION_DAYS=setting):
                self.assertEqual(self.upload().status_code, 503)
        with override_settings(
            ATTACHMENT_STORAGE_BACKEND="s3", ATTACHMENT_S3_BUCKET=""
        ):
            self.assertEqual(self.upload().status_code, 503)
        self.assertFalse(Attachment.objects.exists())
        self.assertFalse(
            OperationRequest.objects.filter(response__isnull=True).exists()
        )

    def test_registration_failure_reuses_verified_object_on_retry(self):
        key = str(uuid4())
        with patch(
            "apps.attachments.services.Attachment.objects.create",
            side_effect=RuntimeError("injected DB failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.upload(key=key)
        self.assertFalse(Attachment.objects.exists())
        self.assertEqual(len(list(self.root.rglob("*.png"))), 1)
        response = self.upload(key=key)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(list(self.root.rglob("*.png"))), 1)

    def test_download_tenant_and_actor_scope_precedes_object_access(self):
        own = self.attachment(self.user)
        other = self.attachment(self.other)
        response = self.request(
            "get", f"/api/v1/attachments/{own.pk}/download/", user=self.user
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), png())
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        with patch("apps.attachments.storage.backend") as store:
            denied = self.request(
                "get", f"/api/v1/attachments/{other.pk}/download/", user=self.user
            )
            self.assertEqual(denied.status_code, 404)
            store.assert_not_called()
        self.assertEqual(
            self.client.get(f"/api/v1/attachments/{own.pk}/download/").status_code, 401
        )

    def test_native_sql_lookup_has_tenant_and_actor_filters(self):
        own = self.attachment(self.user)
        admin = self.attachment()
        other = self.attachment(self.other)
        response = self.request(
            "get",
            "/api/v1/attachments/",
            {"ids": f"{own.pk},{admin.pk},{other.pk}"},
            self.user,
        )
        self.assertEqual([r["id"] for r in response.json()["records"]], [own.pk])
        self.assertEqual(services.raw_metadata(self.principal(), [other.pk]), [])
        self.assertEqual(
            self.request(
                "get", "/api/v1/attachments/", {"ids": "1);SELECT 1--"}
            ).status_code,
            422,
        )

    def test_cookie_upload_requires_csrf_and_bearer_upload_requires_key(self):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["identity"] = self.claims[self.admin.pk]
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
        client.get("/attachments/upload/")
        denied = client.post(
            "/api/v1/attachments/",
            {"file": SimpleUploadedFile("proof.png", png(), "image/png")},
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(denied.status_code, 403, denied.content)
        accepted = client.post(
            "/api/v1/attachments/",
            {"file": SimpleUploadedFile("proof.png", png(), "image/png")},
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(accepted.status_code, 201, accepted.content)
        missing_key = self.client.post(
            "/api/v1/attachments/",
            {"file": SimpleUploadedFile("proof.png", png(), "image/png")},
            HTTP_AUTHORIZATION="Bearer " + access_token(self.principal()),
        )
        self.assertEqual(missing_key.status_code, 400, missing_key.content)
        self.assertEqual(
            missing_key.json()["error"]["code"], "IDEMPOTENCY_KEY_REQUIRED"
        )

    def test_application_owner_can_read_admin_evidence_until_withdrawal(self):
        self.receive(1, fixed=True)
        from apps.inventory.models import AssetCard

        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        issued = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(issued, 1, [card.pk]).status_code, 200)
        document = self.create(
            "scrap",
            "loss",
            [{"item_id": self.fixed.pk, "asset_card_id": card.pk}],
            self.user,
            reason="本人遗失申请",
        )
        obj = self.attachment()
        self.assertEqual(self.link(document, obj).status_code, 200)
        self.assertEqual(
            self.request(
                "get", f"/api/v1/attachments/{obj.pk}/", user=self.user
            ).status_code,
            200,
        )
        self.assertEqual(
            self.request(
                "get", "/api/v1/attachments/", {"ids": str(obj.pk)}, self.user
            ).json()["records"][0]["id"],
            obj.pk,
        )
        self.assertEqual(self.link(document, obj, "delete", "误关联").status_code, 200)
        self.assertEqual(
            self.request(
                "get", f"/api/v1/attachments/{obj.pk}/", user=self.user
            ).status_code,
            404,
        )

    def test_celery_entry_revalidates_scope_and_session(self):
        own, other = self.attachment(self.user), self.attachment(self.other)
        scope = self.claims[self.user.pk]
        with patch("apps.attachments.storage.backend") as store:
            result = verify_integrity.apply(args=[scope, other.pk]).get()
            self.assertFalse(result["ok"])
            store.assert_not_called()
        result = verify_integrity.apply(args=[scope, own.pk]).get()
        self.assertTrue(result["ok"])
        forged = {**scope, "tid": self.other_tenant.pk}
        self.assertFalse(verify_integrity.apply(args=[forged, other.pk]).get()["ok"])
        AuthSession.objects.filter(
            tenant_id=self.tenant.pk, user_id=self.user.pk
        ).update(revoked_at=timezone.now())
        self.assertFalse(verify_integrity.apply(args=[scope, own.pk]).get()["ok"])

    def test_link_withdraw_replay_preserves_object_and_metadata(self):
        obj, document = self.attachment(), self.scrap()
        self.assertEqual(self.link(document, obj).status_code, 200)
        key = str(uuid4())
        for _ in range(2):
            response = self.link(document, obj, "delete", "误关联", key=key)
            self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(ScrapAttachment.objects.exists())
        self.assertIsNone(Attachment.objects.get(pk=obj.pk).deleted_at)
        self.assertTrue((self.root / obj.object_key).exists())
        self.assertEqual(self.link(document, obj).status_code, 200)
        self.assertEqual(self.link(document, obj, "delete").status_code, 422)

    def test_decided_orders_freeze_addition_and_withdrawal(self):
        self.receive(10)
        for action, payload in [
            ("approve", {}),
            ("reject", {"reason": "证据不足"}),
            ("cancel", {"reason": "取消"}),
        ]:
            with self.subTest(action=action):
                first, second, document = (
                    self.attachment(),
                    self.attachment(),
                    self.scrap(),
                )
                self.assertEqual(self.link(document, first).status_code, 200)
                self.assertEqual(
                    self.act("scrap", document, action, payload).status_code, 200
                )
                self.assertEqual(self.link(document, second).status_code, 409)
                self.assertEqual(
                    self.link(document, first, "delete", "迟到撤回").status_code, 409
                )
                self.assertTrue(
                    ScrapAttachment.objects.filter(
                        scrap_id=document["id"], attachment_id=first.pk
                    ).exists()
                )

    def test_bulk_import_is_all_or_nothing_across_tenants(self):
        own, foreign, document = (
            self.attachment(),
            self.attachment(self.other),
            self.scrap(),
        )
        data = f"scrap_id,attachment_id\n{document['id']},{own.pk}\n{document['id']},{foreign.pk}\n".encode()
        response = self.multipart(
            "/api/v1/attachments/import/",
            {"file": SimpleUploadedFile("links.csv", data, "text/csv")},
        )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(ScrapAttachment.objects.exists())
        valid = f"scrap_id,attachment_id\n{document['id']},{own.pk}\n".encode()
        key = str(uuid4())
        for _ in range(2):
            response = self.multipart(
                "/api/v1/attachments/import/",
                {"file": SimpleUploadedFile("links.csv", valid, "text/csv")},
                key=key,
            )
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(ScrapAttachment.objects.count(), 1)

    def test_bulk_csv_rejects_unknown_columns_duplicate_and_bad_ids(self):
        for data in [
            b"tenant_id,scrap_id,attachment_id\n1,1,1",
            b"scrap_id,attachment_id\n1,1\n1,1",
            b"scrap_id,attachment_id\n1,-2",
            b"scrap_id,attachment_id\n1,1,extra",
        ]:
            with self.subTest(data=data), self.assertRaises(BusinessError):
                services.import_csv(self.principal(), str(uuid4()), data)

    def test_key_prefix_and_content_integrity_fail_closed(self):
        own, other = self.attachment(), self.attachment(self.other)
        forged = Attachment.objects.create(
            tenant_id=self.tenant.pk,
            object_key=other.object_key,
            sha256=other.sha256,
            size_bytes=other.size_bytes,
            content_type=other.content_type,
            retention_until=other.retention_until,
            uploaded_by=self.admin.pk,
        )
        with patch("apps.attachments.storage.backend") as store:
            self.assertEqual(
                self.request(
                    "get", f"/api/v1/attachments/{forged.pk}/download/"
                ).status_code,
                404,
            )
            store.assert_not_called()
        (self.root / own.object_key).write_bytes(b"corrupt")
        self.assertEqual(
            self.request("get", f"/api/v1/attachments/{own.pk}/download/").status_code,
            409,
        )

    def test_legal_hold_is_one_way_and_metadata_immutable(self):
        obj = self.attachment()
        path = f"/api/v1/attachments/{obj.pk}/hold/"
        self.assertEqual(
            self.request("post", path, {"reason": "争议保全"}, self.user).status_code,
            403,
        )
        response = self.request("post", path, {"reason": "争议保全"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self.request(
                "post", path, {"reason": "解除", "legal_hold": False}
            ).status_code,
            422,
        )
        for changes in [
            {"legal_hold": False},
            {"sha256": "1" * 64},
            {"retention_until": timezone.now()},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(DatabaseError),
                transaction.atomic(),
            ):
                Attachment.objects.filter(pk=obj.pk).update(**changes)

    def test_local_storage_blocks_public_roots_and_symlinks(self):
        with override_settings(
            ATTACHMENT_STORAGE_ROOT=str(Path(__file__).resolve().parents[1] / "static")
        ):
            with self.assertRaises(BusinessError):
                storage.backend()
        obj = self.attachment()
        path = self.root / obj.object_key
        path.unlink()
        path.symlink_to("/etc/passwd")
        self.assertEqual(
            self.request("get", f"/api/v1/attachments/{obj.pk}/download/").status_code,
            404,
        )

    def test_db_composite_fk_blocks_cross_tenant_evidence(self):
        obj, document = self.attachment(self.other), self.scrap()
        with self.assertRaises(DatabaseError), transaction.atomic():
            ScrapAttachment.objects.create(
                tenant_id=self.tenant.pk, scrap_id=document["id"], attachment_id=obj.pk
            )

    def test_html_upload_and_scrap_evidence_rendering(self):
        token = access_token(self.principal())
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token}
        response = self.client.post(
            "/attachments/upload/",
            {
                "idempotency_key": str(uuid4()),
                "file": SimpleUploadedFile("proof.png", png(), "image/png"),
            },
            **headers,
        )
        self.assertEqual(response.status_code, 302, response.content)
        obj, document = Attachment.objects.get(), self.scrap()
        self.assertEqual(self.link(document, obj).status_code, 200)
        page = self.client.get(f"/scrap/{document['id']}/", **headers)
        self.assertContains(page, "证据附件")
        self.assertContains(page, f"/attachments/{obj.pk}/download/")


class S3ContractTests(TestCase):
    def test_existing_s3_object_is_verified_and_corruption_rejected(self):
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="p6-fixture",
            aws_secret_access_key="synthetic-not-a-real-key",
        )
        key = "tenants/1/evidence/" + "a" * 64 + ".png"
        evidence = Evidence(
            png(), hashlib.sha256(png()).hexdigest(), "image/png", ".png"
        )
        with Stubber(client) as stub:
            stub.add_client_error(
                "put_object",
                service_error_code="PreconditionFailed",
                http_status_code=412,
            )
            stub.add_response(
                "get_object",
                {"Body": StreamingBody(BytesIO(evidence.data), len(evidence.data))},
            )
            store = storage.S3Store(client, "private-fixture")
            store.put(1, key, evidence)
            stub.add_response("get_object", {"Body": StreamingBody(BytesIO(b"bad"), 3)})
            with self.assertRaises(BusinessError) as error:
                store.read(1, key, evidence.sha256, len(evidence.data))
            self.assertEqual(error.exception.code, "OBJECT_INTEGRITY_ERROR")
            stub.assert_no_pending_responses()

    def test_s3_conditional_private_upload_and_verified_download(self):
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="p6-fixture",
            aws_secret_access_key="synthetic-not-a-real-key",
        )
        key = "tenants/1/evidence/" + "a" * 64 + ".png"
        evidence = Evidence(
            png(), hashlib.sha256(png()).hexdigest(), "image/png", ".png"
        )
        with Stubber(client) as stub:
            stub.add_response(
                "put_object",
                {},
                {
                    "Bucket": "private-fixture",
                    "Key": key,
                    "Body": evidence.data,
                    "ContentType": "image/png",
                    "ContentLength": len(evidence.data),
                    "Metadata": {"sha256": evidence.sha256, "tenant": "1"},
                    "ServerSideEncryption": "AES256",
                    "IfNoneMatch": "*",
                },
            )
            stub.add_response(
                "get_object",
                {"Body": StreamingBody(BytesIO(evidence.data), len(evidence.data))},
                {"Bucket": "private-fixture", "Key": key},
            )
            store = storage.S3Store(client, "private-fixture")
            store.put(1, key, evidence)
            self.assertEqual(
                store.read(1, key, evidence.sha256, len(evidence.data)), evidence.data
            )
            stub.assert_no_pending_responses()

    def test_s3_wrong_prefix_is_rejected_before_sdk_call(self):
        from unittest.mock import Mock

        client = Mock()
        store = storage.S3Store(client, "private-fixture")
        with self.assertRaises(BusinessError):
            store.read(2, "tenants/1/evidence/" + "a" * 64 + ".png", "a" * 64, 1)
        client.get_object.assert_not_called()
