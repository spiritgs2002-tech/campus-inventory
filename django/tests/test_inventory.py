from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from apps.core.models import Tenant, OperationRequest
from apps.catalog.models import Location
from apps.inventory import assets, queries
from apps.reconcile import services as reconciliation
from apps.inventory.models import (
    AssetCard,
    AssetEvent,
    Inventory,
    InboundItem,
    OutboundItem,
    OutboundAsset,
)
from shared.auth.backends import authorize_claims
from shared.errors import BusinessError
from .order_fixtures import OrderFixtureMixin


class InventoryTests(OrderFixtureMixin, TestCase):
    def principal(self, user=None):
        return authorize_claims(self.claims[(user or self.admin).pk])

    def checks(self):
        with connection.cursor() as cursor:
            result = reconciliation.collect(cursor, 3)
        self.assertEqual(len(result["checks"]), 13)
        return {row["name"]: row["count"] for row in result["checks"]}

    def repair(self, card, action, data=None, user=None, key=None):
        return self.request(
            "post",
            f"/api/v1/assets/{card.pk}/{action}/",
            data or {"reason": "维修验证"},
            user,
            key,
        )

    def test_stock_admin_numbers_user_availability_and_tenant_scope(self):
        self.receive()
        admin = self.request("get", "/api/v1/stock/")
        self.assertEqual(admin.status_code, 200, admin.content)
        self.assertEqual(admin.json()["records"][0]["quantity"], "50.000")
        user = self.request("get", "/api/v1/stock/", user=self.user)
        self.assertEqual(user.status_code, 200, user.content)
        row = user.json()["records"][0]
        self.assertTrue(row["available"])
        self.assertFalse(row["low_stock"])
        for field in ["quantity", "version", "location_id", "original_value"]:
            self.assertNotIn(field, row)
        self.assertEqual(
            self.request("get", "/api/v1/stock/", user=self.other).json()["total"], 0
        )
        self.assertEqual(
            self.request(
                "get", "/api/v1/stock/", {"tenant_id": self.other_tenant.pk}
            ).status_code,
            422,
        )

    def test_missing_or_damaged_stock_is_not_available_and_low_filter(self):
        self.create(
            "inbound",
            "opening",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "10",
                    "location_id": self.location.pk,
                    "quality": "damaged",
                }
            ],
        )
        result = self.request("get", "/api/v1/stock/", {"low": "true"}, self.user)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(result.json()["records"][0]["available"])
        self.assertTrue(result.json()["records"][0]["low_stock"])
        self.receive(2)
        self.assertEqual(
            self.request("get", "/api/v1/stock/", {"low": "true"}, self.user).json()[
                "total"
            ],
            0,
        )

    def test_read_validation_and_ledger_admin_only(self):
        self.receive()
        self.assertEqual(
            self.request("get", "/api/v1/stock/ledger/", user=self.user).status_code,
            403,
        )
        result = self.request("get", "/api/v1/stock/ledger/")
        self.assertEqual(result.json()["total"], 1)
        self.assertEqual(result.json()["records"][0]["balance_after"], "50.000")
        for data in [{"page": "x"}, {"quality": "invalid"}, {"item_id": -1}]:
            self.assertEqual(
                self.request("get", "/api/v1/stock/", data).status_code, 422
            )
        for path in ["/api/v1/stock/", "/api/v1/stock/ledger/", "/api/v1/assets/"]:
            self.assertEqual(self.request("put", path, {}).status_code, 405)

    def test_stock_queries_are_paginated_without_n_plus_one(self):
        self.receive()
        for index in range(28):
            location = Location.objects.create(
                tenant_id=self.tenant.pk, name=f"库位{index}"
            )
            Inventory.objects.create(
                tenant_id=self.tenant.pk,
                item_id=self.item.pk,
                location_id=location.pk,
                quality="available",
            )
        p = self.principal()
        with CaptureQueriesContext(connection) as captured:
            result, _ = queries.stock(p, {})
        self.assertEqual((result["total"], len(result["records"])), (29, 25))
        self.assertEqual(len(captured), 3)

    def test_asset_access_and_scan_never_bypass_authentication(self):
        self.receive(2, fixed=True)
        card = AssetCard.objects.filter(tenant_id=self.tenant.pk).first()
        self.assertEqual(
            self.request(
                "get", f"/api/v1/assets/{card.pk}/", user=self.user
            ).status_code,
            404,
        )
        issued = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(issued, 1, [card.pk]).status_code, 200)
        result = self.request("get", "/api/v1/assets/", user=self.user)
        self.assertEqual(result.json()["total"], 1)
        scan = f"/api/v1/assets/scan/{card.qr_token}/"
        self.assertEqual(self.client.get(scan).status_code, 401)
        self.assertEqual(self.request("get", scan, user=self.user).status_code, 200)
        self.assertEqual(self.request("get", scan, user=self.other).status_code, 404)
        self.assertEqual(self.request("get", scan + "notfound/").status_code, 404)
        detail = self.request(
            "get", f"/api/v1/assets/{card.pk}/", user=self.user
        ).json()
        self.assertNotIn("original_value", detail["record"])
        for event in detail["events"]["records"]:
            for field in [
                "responsible_user_id",
                "operator_id",
                "operation_id",
                "reason",
            ]:
                self.assertNotIn(field, event)
        relation = OutboundAsset.objects.get(asset_card_id=card.pk)
        self.create(
            "inbound",
            "return",
            [
                {
                    "item_id": self.fixed.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "quality": "available",
                    "source_outbound_asset_id": relation.pk,
                }
            ],
        )
        self.assertEqual(self.request("get", scan, user=self.user).status_code, 404)

    def test_repair_cycle_event_reason_and_idempotency(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        key = str(uuid4())
        for replayed in ["false", "true"]:
            response = self.repair(card, "repair_start", key=key)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response["Idempotency-Replayed"], replayed)
        self.assertEqual(
            AssetEvent.objects.filter(
                asset_card_id=card.pk, action="repair_start"
            ).count(),
            1,
        )
        self.assertEqual(self.repair(card, "repair_start").status_code, 409)
        response = self.repair(card, "repair_finish")
        self.assertEqual(response.status_code, 200, response.content)
        card.refresh_from_db()
        self.assertEqual(card.status, "in_stock")
        self.assertEqual(
            AssetEvent.objects.filter(asset_card_id=card.pk).latest("id").reason,
            "维修验证",
        )
        self.assertFalse(any(self.checks().values()))

    def test_repair_permissions_terminal_and_location_validation(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        self.assertEqual(
            self.repair(card, "repair_start", user=self.user).status_code, 403
        )
        self.assertEqual(
            self.repair(card, "repair_start", user=self.other).status_code, 404
        )
        self.assertEqual(self.repair(card, "repair_finish").status_code, 409)
        self.assertEqual(
            self.repair(card, "repair_start", {"reason": ""}).status_code, 422
        )
        self.assertEqual(self.repair(card, "repair_start").status_code, 200)
        bad = self.repair(
            card,
            "repair_finish",
            {"reason": "完成", "location_id": self.other_location.pk},
        )
        self.assertEqual(bad.status_code, 404)
        card.refresh_from_db()
        self.assertEqual(card.status, "repair")
        document = self.create(
            "scrap",
            "scrap",
            [{"item_id": self.fixed.pk, "asset_card_id": card.pk}],
            reason="无法维修",
        )
        self.assertEqual(self.act("scrap", document, "approve").status_code, 200)
        self.assertEqual(self.repair(card, "repair_finish").status_code, 409)

    def test_failed_event_rolls_back_repair_and_operation(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        count = OperationRequest.objects.count()
        with patch(
            "apps.inventory.posting.event",
            side_effect=RuntimeError("injected event failure"),
        ):
            with self.assertRaises(RuntimeError):
                assets.repair(
                    self.principal(),
                    card.pk,
                    "repair_start",
                    str(uuid4()),
                    {"reason": "故障注入"},
                )
        card.refresh_from_db()
        self.assertEqual(card.status, "in_stock")
        self.assertEqual(OperationRequest.objects.count(), count)
        self.assertEqual(AssetEvent.objects.filter(asset_card_id=card.pk).count(), 1)

    def test_inventory_version_is_database_managed_and_dimensions_frozen(self):
        self.receive()
        row = Inventory.objects.get(tenant_id=self.tenant.pk)
        initial = row.version
        Inventory.objects.filter(pk=row.pk).update(quantity=Decimal(50))
        row.refresh_from_db()
        self.assertEqual(row.version, initial)
        for amount in [49, 50]:
            Inventory.objects.filter(pk=row.pk).update(quantity=amount)
        row.refresh_from_db()
        self.assertEqual((row.quantity, row.version), (50, initial + 2))
        for field, value in [
            ("version", 999),
            ("quality", "damaged"),
            ("location_id", self.other_location.pk),
        ]:
            with (
                self.subTest(field=field),
                self.assertRaises(DatabaseError),
                transaction.atomic(),
            ):
                Inventory.objects.filter(pk=row.pk).update(**{field: value})
        self.assertEqual(Inventory.objects.get(pk=row.pk).version, initial + 2)

    def test_runtime_history_and_asset_immutability_cannot_be_disabled(self):
        self.receive()
        self.receive(1, fixed=True)
        for table in ["stock_ledger", "asset_events", "asset_cards"]:
            for sql in [
                f"DELETE FROM {table}",
                f"TRUNCATE {table}",
                f"ALTER TABLE {table} DISABLE TRIGGER USER",
            ]:
                with (
                    self.subTest(sql=sql),
                    self.assertRaises(DatabaseError),
                    transaction.atomic(),
                ):
                    with connection.cursor() as cursor:
                        cursor.execute(sql)
        for table in ["stock_ledger", "asset_events"]:
            with (
                self.assertRaises(DatabaseError),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(f"UPDATE {table} SET created_at=now()")
        for field, value in [
            ("asset_no", "changed"),
            ("qr_token", "changed"),
            ("original_value", 1),
            ("source_inbound_item_id", 1),
        ]:
            card = AssetCard.objects.get(tenant_id=self.tenant.pk)
            if field == "source_inbound_item_id":
                value = card.source_inbound_item_id + 1000
            with (
                self.subTest(field=field),
                self.assertRaises(DatabaseError),
                transaction.atomic(),
            ):
                AssetCard.objects.filter(pk=card.pk).update(**{field: value})

    def test_asset_allocation_and_qr_entropy_and_rollback(self):
        start = Tenant.objects.get(pk=self.tenant.pk).next_asset_number
        self.receive(3, fixed=True)
        cards = list(AssetCard.objects.filter(tenant_id=self.tenant.pk).order_by("id"))
        self.assertEqual(
            [c.asset_no for c in cards],
            [f"{self.tenant.asset_prefix}-{n:08d}" for n in range(start, start + 3)],
        )
        self.assertEqual(len({c.qr_token for c in cards}), 3)
        self.assertTrue(all(len(c.qr_token) >= 22 for c in cards))
        with patch(
            "apps.inventory.posting.event",
            side_effect=RuntimeError("creation event failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.receive(2, fixed=True)
        self.assertEqual(AssetCard.objects.count(), 3)
        self.assertEqual(
            Tenant.objects.get(pk=self.tenant.pk).next_asset_number, start + 3
        )

    def mixed_scenario(self):
        self.receive(50)
        purchase = self.receive(5, purchase=True)
        source = InboundItem.objects.get(order_id=purchase["id"])
        self.create(
            "outbound",
            "purchase_return",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "source_inbound_item_id": source.pk,
                }
            ],
        )
        issued = self.requisition(10)
        self.assertEqual(self.execute(issued, 10).status_code, 200)
        line = OutboundItem.objects.get(order_id=issued["id"])
        self.create(
            "inbound",
            "return",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "quality": "available",
                    "source_outbound_item_id": line.pk,
                }
            ],
        )
        damage = self.create(
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
            reason="耗材破损",
        )
        self.assertEqual(self.act("scrap", damage, "approve").status_code, 200)
        count = self.create(
            "adjustment",
            "count",
            [
                {
                    **self.snapshot(),
                    "quantity": "1",
                    "direction": "in",
                    "counted_qty": str(self.stock() + 1),
                }
            ],
            reason="盘点",
            reason_category="gain",
        )
        self.assertEqual(self.act("adjustment", count, "approve").status_code, 200)
        conversion = self.create(
            "adjustment",
            "quality_transfer",
            [
                {
                    **self.snapshot(),
                    "quantity": "2",
                    "direction": "out",
                    "to_quality": "damaged",
                    "target_inventory_version": 0,
                }
            ],
            reason="质量转换",
            reason_category="quality_change",
        )
        self.assertEqual(self.act("adjustment", conversion, "approve").status_code, 200)
        self.receive(3, fixed=True)
        cards = list(AssetCard.objects.filter(tenant_id=self.tenant.pk).order_by("id"))
        fixed = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(fixed, 1, [cards[0].pk]).status_code, 200)
        scrap = self.create(
            "scrap",
            "scrap",
            [{"item_id": self.fixed.pk, "asset_card_id": cards[1].pk}],
            reason="报废",
        )
        self.assertEqual(self.act("scrap", scrap, "approve").status_code, 200)
        loss = self.create(
            "scrap",
            "loss",
            [{"item_id": self.fixed.pk, "asset_card_id": cards[2].pk}],
            reason="遗失",
        )
        self.assertEqual(self.act("scrap", loss, "approve").status_code, 200)
        gain = self.create(
            "adjustment",
            "count",
            [
                {
                    "item_id": self.fixed.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "quality": "available",
                    "direction": "in",
                    "observed_at": timezone.now().isoformat(),
                }
            ],
            reason="固定资产盘盈",
            reason_category="gain",
        )
        self.assertEqual(self.act("adjustment", gain, "approve").status_code, 200)
        return cards

    def test_mixed_business_data_passes_all_thirteen_checks_and_old_sql_false_positive(
        self,
    ):
        self.mixed_scenario()
        self.assertFalse(any(self.checks().values()))
        old_path = Path(settings.BASE_DIR).parent / "v7-tools" / "reconcile.sql"
        if old_path.exists():
            old = (
                old_path.read_text()
                .split(
                    "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;", 1
                )[1]
                .rsplit("COMMIT;", 1)[0]
            )
            defects = []
            with connection.cursor() as cursor:
                for statement in old.split(";"):
                    if statement.strip():
                        cursor.execute(statement)
                        defects += [row[0] for row in cursor.fetchall()]
            self.assertIn("inbound_ledger", defects)
            self.assertIn("outbound_ledger", defects)
            self.assertIn("scrap_ledger", defects)
            self.assertIn("adjustment_ledger", defects)

    def test_versioned_reconciliation_source_matches_packaged_copy(self):
        self.assertEqual(
            tuple(name for name, _ in reconciliation.statements()),
            reconciliation.CHECKS,
        )
        source = Path(settings.BASE_DIR).parent / "v7-tools" / "reconcile-v2.sql"
        if source.exists():
            self.assertEqual(
                source.read_bytes(),
                (settings.SQL_ROOT / "reconcile-v2.sql").read_bytes(),
            )

    def test_event_responsibility_mismatch_is_detected(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        issued = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(issued, 1, [card.pk]).status_code, 200)
        AssetCard.objects.filter(pk=card.pk).update(
            responsible_user_id=self.approver.pk
        )
        self.assertEqual(self.checks()["asset_latest_event"], 1)

    def test_all_thirteen_checks_detect_seeded_corruption(self):
        cards = self.mixed_scenario()
        in_stock = cards[2].pk
        mutations = {
            "inventory_ledger": (
                "inventory",
                "UPDATE inventory SET quantity=quantity+1 WHERE quality='available'",
            ),
            "asset_latest_event": (
                "asset_cards",
                f"UPDATE asset_cards SET status='repair' WHERE id={in_stock}",
            ),
            "asset_open_issues": (
                "asset_cards",
                f"UPDATE asset_cards SET status='in_stock',responsible_user_id=NULL WHERE id={cards[0].pk}",
            ),
            "fixed_issued_count": (
                "outbound_items",
                "UPDATE outbound_items SET requested_qty=requested_qty+1,actual_qty=actual_qty+1 WHERE item_type='fixed'",
            ),
            "fixed_created_count": (
                "inbound_items",
                "UPDATE inbound_items SET quantity=quantity+1 WHERE item_type='fixed'",
            ),
            "excess_consumable_return": (
                "inbound_items",
                "UPDATE inbound_items SET quantity=100 WHERE source_outbound_item_id IS NOT NULL",
            ),
            "quality_transfer_ledger": (
                "stock_ledger",
                "DELETE FROM stock_ledger WHERE adjustment_item_id IN (SELECT id FROM adjustment_items WHERE adjustment_type='quality_transfer')",
            ),
            "inbound_ledger": (
                "stock_ledger",
                "DELETE FROM stock_ledger WHERE inbound_item_id IS NOT NULL",
            ),
            "outbound_ledger": (
                "stock_ledger",
                "DELETE FROM stock_ledger WHERE outbound_item_id IS NOT NULL",
            ),
            "scrap_ledger": (
                "stock_ledger",
                "DELETE FROM stock_ledger WHERE scrap_item_id IS NOT NULL",
            ),
            "adjustment_ledger": (
                "stock_ledger",
                "DELETE FROM stock_ledger WHERE adjustment_item_id IN (SELECT id FROM adjustment_items WHERE adjustment_type='count')",
            ),
            "ledger_balance_after": (
                "stock_ledger",
                "UPDATE stock_ledger SET balance_after=balance_after+1",
            ),
            "purchase_return_excess": (
                "outbound_items",
                "UPDATE outbound_items SET actual_qty=100,requested_qty=100 WHERE source_inbound_item_id IS NOT NULL",
            ),
        }
        # Only the test DB owner injects faults; each fault and temporary trigger
        # change is rolled back. The diagnostic itself runs as inventory_runtime.
        for name, (table, statement) in mutations.items():
            with self.subTest(check=name), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("RESET ROLE")
                    cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
                    cursor.execute(statement)
                    cursor.execute("SET LOCAL ROLE inventory_runtime")
                self.assertGreater(self.checks()[name], 0)
                transaction.set_rollback(True)
        self.assertFalse(any(self.checks().values()))

    def test_state_without_event_is_detected_not_silently_repaired(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        AssetCard.objects.filter(pk=card.pk).update(status="repair")
        self.assertEqual(self.checks()["asset_latest_event"], 1)
        self.assertEqual(AssetEvent.objects.count(), 1)
        self.assertEqual(AssetCard.objects.get(pk=card.pk).status, "repair")

    def test_reconcile_refuses_nested_transactions_and_limits_samples(self):
        with self.assertRaises(BusinessError) as raised:
            reconciliation.run()
        self.assertEqual(raised.exception.code, "RECONCILIATION_REQUIRES_TOP_LEVEL")
        for value in [0, 101]:
            with self.assertRaises(BusinessError):
                reconciliation.run(sample_limit=value)

    def test_html_stock_assets_and_htmx_fragment(self):
        self.receive()
        self.receive(1, fixed=True)
        from shared.auth.tokens import access_token

        headers = {"HTTP_AUTHORIZATION": "Bearer " + access_token(self.principal())}
        page = self.client.get("/stock/", **headers)
        self.assertContains(page, "耗材库存")
        self.assertContains(page, "50.000")
        self.assertContains(page, ">物资:</label>")
        self.assertNotContains(page, "Item id:")
        fragment = self.client.get("/assets/", HTTP_HX_REQUEST="true", **headers)
        self.assertContains(fragment, "电脑")
        self.assertNotContains(fragment, "<!doctype")
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        self.assertContains(
            self.client.get(f"/assets/{card.pk}/repair_start/", **headers), "维修说明"
        )
