from io import BytesIO, StringIO
from uuid import uuid4
from unittest.mock import patch
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from PIL import Image
from apps.catalog.models import Item
from apps.inventory import qr
from shared.auth.backends import authorize_claims
from .order_fixtures import OrderFixtureMixin


class QRTests(OrderFixtureMixin, TestCase):
    def token(self):
        qr.generate_item_tokens(
            authorize_claims(self.claims[self.admin.pk]), uuid4().hex
        )
        self.item.refresh_from_db()
        return self.item.qr_token

    def test_v_qr_1_admin_png_is_valid_image_and_url_comes_from_request(self):
        token = self.token()
        original = qr.qr_png
        with patch("apps.inventory.pickup_views.qr.qr_png", wraps=original) as encoder:
            response = self.request("get", f"/api/v1/qr/{token}/png")
        self.assertEqual(response.status_code, 200, response.content[:100])
        self.assertEqual(response["Content-Type"], "image/png")
        image = Image.open(BytesIO(response.content))
        self.assertEqual(image.format, "PNG")
        self.assertGreater(image.width, 100)
        encoder.assert_called_once_with(f"http://testserver/s/pickup/{token}/")

    def test_teacher_and_anonymous_cannot_print_or_generate_tokens(self):
        token = self.token()
        self.assertEqual(
            self.request("get", f"/api/v1/qr/{token}/png", {}, self.user).status_code,
            403,
        )
        self.assertEqual(self.client.get(f"/api/v1/qr/{token}/png").status_code, 401)
        self.assertEqual(
            self.request("get", "/s/labels/", {}, self.user).status_code, 403
        )
        self.assertEqual(
            self.request("post", "/s/labels/", {}, self.user).status_code, 403
        )

    def test_png_scope_checks_foreign_school_before_encoding(self):
        token = self.token()
        with patch("apps.inventory.pickup_views.qr.qr_png") as encoder:
            response = self.request("get", f"/api/v1/qr/{token}/png", {}, self.other)
        self.assertEqual(response.status_code, 404)
        encoder.assert_not_called()

    def test_backfill_is_idempotent_tenant_scoped_and_never_rotates_existing_tokens(
        self,
    ):
        p = authorize_claims(self.claims[self.admin.pk])
        first, replay = qr.generate_item_tokens(p, "qr-key")
        self.item.refresh_from_db()
        saved = self.item.qr_token
        second, replay2 = qr.generate_item_tokens(p, "qr-key")
        self.assertEqual(first, second)
        self.assertFalse(replay)
        self.assertTrue(replay2)
        self.assertEqual(first["generated"], 1)
        self.assertEqual(qr.generate_item_tokens(p, "qr-next")[0]["generated"], 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.qr_token, saved)
        self.assertGreaterEqual(len(saved), 32)

    def test_admin_labels_get_is_read_only_and_post_generates_then_prints(self):
        page = self.request("get", "/s/labels/")
        self.assertContains(page, "尚未生成二维码")
        self.item.refresh_from_db()
        self.assertIsNone(self.item.qr_token)
        page = self.request("post", "/s/labels/", {"idempotency_key": "labels"})
        # JSON body is intentionally not a HTML form; no implicit parsing.
        self.assertEqual(page.status_code, 400)
        from shared.auth.tokens import access_token

        token = access_token(authorize_claims(self.claims[self.admin.pk]))
        generated = self.client.post(
            "/s/labels/",
            {"idempotency_key": "labels"},
            HTTP_AUTHORIZATION="Bearer " + token,
        )
        self.assertEqual(generated.status_code, 200, generated.content)
        self.item.refresh_from_db()
        self.assertContains(generated, f"/api/v1/qr/{self.item.qr_token}/png")
        self.assertContains(
            self.request("get", "/s/labels/", {"q": "不存在"}), "暂无耗材"
        )

    def test_cli_uses_scoped_operations_actor_and_dry_run_does_not_write(self):
        output = StringIO()
        with override_settings(MAINTENANCE_ACTORS={self.tenant.pk: self.admin.pk}):
            call_command(
                "generate_item_qr",
                tenant_id=self.tenant.pk,
                dry_run=True,
                stdout=output,
            )
            self.item.refresh_from_db()
            self.assertIsNone(self.item.qr_token)
            call_command(
                "generate_item_qr",
                tenant_id=self.tenant.pk,
                idempotency_key="cli",
                stdout=output,
            )
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.qr_token)

    def test_unique_index_rejects_duplicate_tokens_inside_one_school(self):
        token = self.token()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(
                tenant_id=self.tenant.pk,
                category_id=self.item.category_id,
                code="DUP",
                name="dup",
                type="consumable",
                unit="包",
                qr_token=token,
            )
