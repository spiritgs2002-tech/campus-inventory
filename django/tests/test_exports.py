"""Administrator CSV exports for stock, ledger and assets (remediation)."""

import csv
import io
from unittest.mock import patch
from django.test import TestCase
from shared.auth.backends import authorize_claims
from shared.auth.tokens import access_token
from apps.inventory import exports
from .order_fixtures import OrderFixtureMixin


class ExportTests(OrderFixtureMixin, TestCase):
    def get(self, path, user=None):
        token = access_token(authorize_claims(self.claims[(user or self.admin).pk]))
        return self.client.get(path, HTTP_AUTHORIZATION="Bearer " + token)

    def rows(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("attachment;", response["Content-Disposition"])
        text = response.content.decode("utf-8")
        self.assertTrue(text.startswith("﻿"))
        return list(csv.reader(io.StringIO(text[1:])))

    def test_admin_exports_stock_ledger_and_assets(self):
        self.receive(7)
        self.receive(quantity=1, fixed=True)
        stock = self.rows(self.get("/stock/export.csv"))
        self.assertEqual(stock[0][:2], ["物资编码", "物资名称"])
        self.assertIn(["7.000"], [[row[5]] for row in stock[1:]])
        ledger = self.rows(self.get("/stock/ledger/export.csv"))
        self.assertEqual(ledger[0][0], "流水ID")
        self.assertEqual([row[5] for row in ledger[1:]], ["入库"])
        assets = self.rows(self.get("/assets/export.csv"))
        self.assertEqual(len(assets), 2)
        self.assertEqual(assets[1][1], self.fixed.name)

    def test_export_applies_list_filters_and_ignores_page(self):
        self.receive(7)
        other = self.rows(
            self.get(f"/stock/export.csv?item_id={self.fixed.pk}&page=9")
        )
        self.assertEqual(len(other), 1)
        mine = self.rows(self.get(f"/stock/export.csv?item_id={self.item.pk}"))
        self.assertEqual(len(mine), 2)

    def test_non_admin_and_foreign_tenant_cannot_export(self):
        self.receive(7)
        for path in ("/stock/export.csv", "/stock/ledger/export.csv", "/assets/export.csv"):
            self.assertEqual(self.get(path, self.user).status_code, 403, path)
        foreign = self.rows(self.get("/stock/export.csv", self.other))
        self.assertEqual(len(foreign), 1)

    def test_formula_cells_neutralised_but_numbers_kept(self):
        self.assertEqual(exports.cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(exports.cell("@cmd"), "'@cmd")
        self.assertEqual(exports.cell("-3.000"), "-3.000")
        self.assertEqual(exports.cell(True), "是")
        self.assertEqual(exports.cell(None), "")

    def test_row_cap(self):
        self.receive(7)
        with patch.object(exports, "MAX_ROWS", 0):
            response = self.get("/stock/export.csv")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "EXPORT_TOO_LARGE")
