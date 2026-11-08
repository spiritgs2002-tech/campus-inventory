"""Opening-balance CSV import (remediation)."""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from shared.auth.backends import authorize_claims
from shared.auth.tokens import access_token
from shared.errors import BusinessError
from apps.inventory import opening_import
from apps.inventory.models import AssetCard, InboundOrder, Inventory, StockLedger
from .order_fixtures import OrderFixtureMixin


class OpeningImportTests(OrderFixtureMixin, TestCase):
    def principal(self, user=None):
        return authorize_claims(self.claims[(user or self.admin).pk])

    def csv(self, *rows, header="物资编码,库位,数量,质量,单价"):
        return ("\r\n".join([header, *rows]) + "\r\n").encode("utf-8")

    def line(self, item=None, quantity="5", quality="可用", price="2.50"):
        item = item or self.item
        return f"{item.code},{self.location.name},{quantity},{quality},{price}"

    def stock(self):
        row = Inventory.objects.filter(
            tenant_id=self.tenant.pk, item_id=self.item.pk, quality="available"
        ).first()
        return row.quantity if row else Decimal(0)

    def test_preview_writes_nothing_and_commit_posts_opening(self):
        content = self.csv(self.line(), self.line(self.fixed, "2", "", "300"))
        key = uuid4().hex
        preview = opening_import.run(self.principal(), content, key, preview=True)
        self.assertEqual((preview["error_rows"], len(preview["orders"])), (0, 1))
        self.assertEqual(self.stock(), 0)
        self.assertFalse(InboundOrder.objects.filter(type="opening").exists())
        result = opening_import.run(self.principal(), content, key)
        self.assertEqual(self.stock(), Decimal("5"))
        self.assertEqual(
            AssetCard.objects.filter(tenant_id=self.tenant.pk, item_id=self.fixed.pk).count(),
            2,
        )
        order = InboundOrder.objects.get(pk=result["orders"][0]["id"])
        self.assertEqual(order.type, "opening")
        self.assertTrue(StockLedger.objects.filter(tenant_id=self.tenant.pk).exists())
        replay = opening_import.run(self.principal(), content, key)
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.stock(), Decimal("5"))

    def test_same_file_with_new_key_is_rejected(self):
        content = self.csv(self.line())
        opening_import.run(self.principal(), content, uuid4().hex)
        with self.assertRaises(BusinessError) as caught:
            opening_import.run(self.principal(), content, uuid4().hex)
        self.assertEqual(caught.exception.code, "OPENING_ALREADY_IMPORTED")
        self.assertEqual(self.stock(), Decimal("5"))

    def test_row_errors_reported_and_nothing_written(self):
        content = self.csv(
            self.line(),
            "NOPE,不存在库位,abc,坏,x",
            self.line(self.fixed, "1.5"),
            self.line(),
        )
        result = opening_import.run(self.principal(), content, uuid4().hex)
        self.assertEqual(result["error_rows"], 3)
        self.assertEqual([row["line"] for row in result["errors"]], [3, 4, 5])
        self.assertEqual(len(result["errors"][0]["errors"]), 5)
        self.assertIn("重复", result["errors"][2]["errors"][0])
        self.assertEqual(self.stock(), 0)

    def test_chunks_into_multiple_orders_atomically(self):
        lines = [(dict(n=i), "consumable", Decimal(1)) for i in range(120)]
        self.assertEqual(
            [len(c) for c in opening_import.chunks(lines)], [50, 50, 20]
        )
        fixed = [(dict(n=i), "fixed", Decimal(600)) for i in range(3)]
        self.assertEqual([len(c) for c in opening_import.chunks(fixed)], [1, 1, 1])

    def test_failure_in_later_batch_rolls_back_earlier_batches(self):
        rows = [self.line()] + [
            f"{self.item.code},{self.location.name},1,损坏,1" for _ in range(1)
        ]
        # Force one row per order so the second create call fails after the first.
        original, calls = opening_import.orders.create, []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise BusinessError("INJECTED", "注入失败", 409)
            return original(*args, **kwargs)

        with patch.object(opening_import, "LINES_PER_ORDER", 1), patch.object(
            opening_import.orders, "create", flaky
        ):
            with self.assertRaises(BusinessError):
                opening_import.run(self.principal(), self.csv(*rows), uuid4().hex)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.stock(), 0)
        self.assertFalse(InboundOrder.objects.filter(type="opening").exists())

    def test_gbk_file_and_header_validation(self):
        content = self.csv(self.line()).decode("utf-8").encode("gb18030")
        result = opening_import.run(self.principal(), content, uuid4().hex, preview=True)
        self.assertEqual(result["error_rows"], 0)
        with self.assertRaises(BusinessError) as caught:
            opening_import.run(
                self.principal(), self.csv(self.line(), header="名称,数量"), uuid4().hex
            )
        self.assertEqual(caught.exception.code, "INVALID_HEADER")

    def test_page_admin_only_and_upload(self):
        token = access_token(self.principal())
        auth = {"HTTP_AUTHORIZATION": "Bearer " + token}
        self.assertEqual(self.client.get("/inbound/import/", **auth).status_code, 200)
        template = self.client.get("/inbound/import/template.csv", **auth)
        self.assertIn("物资编码", template.content.decode("utf-8"))
        response = self.client.post(
            "/inbound/import/",
            {
                "idempotency_key": uuid4().hex,
                "mode": "commit",
                "file": SimpleUploadedFile("o.csv", self.csv(self.line()), "text/csv"),
            },
            **auth,
        )
        self.assertContains(response, "导入完成")
        self.assertEqual(self.stock(), Decimal("5"))
        user = {"HTTP_AUTHORIZATION": "Bearer " + access_token(self.principal(self.user))}
        self.assertEqual(self.client.get("/inbound/import/", **user).status_code, 403)
