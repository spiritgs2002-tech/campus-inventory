from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
import psycopg
from django.db import OperationalError
from django.test import TestCase
from django.utils import timezone
from apps.core.models import OperationRequest, AuditLog
from apps.inventory.models import (
    InboundOrder,
    InboundItem,
    OutboundOrder,
    OutboundItem,
    OutboundAsset,
    AssetCard,
    AssetEvent,
    Inventory,
    StockLedger,
    ScrapOrder,
    ScrapItem,
    AdjustmentOrder,
    AdjustmentItem,
)
from shared.auth.backends import authorize_claims
from shared.auth.tokens import access_token


from .order_fixtures import OrderFixtureMixin


class OrderTests(OrderFixtureMixin, TestCase):
    def test_inbound_is_immediate_same_xid_and_atomic_on_bad_line(self):
        doc = self.receive()
        header = InboundOrder.objects.get(tenant_id=self.tenant.pk, pk=doc["id"])
        self.assertTrue(str(header.created_xid).isdigit())
        self.assertEqual(self.stock(), 50)
        self.assertEqual(StockLedger.objects.count(), 1)
        audit = AuditLog.objects.get(object_type="inbound", object_id=header.pk)
        self.assertEqual(audit.action, "inbound.create")
        self.assertEqual(audit.changes["after"]["action"], "create")
        before = (InboundOrder.objects.count(), OperationRequest.objects.count())
        bad = self.request(
            "post",
            "/api/v1/inbound/",
            {
                "type": "opening",
                "lines": [
                    {
                        "item_id": self.item.pk,
                        "quantity": "1",
                        "location_id": self.location.pk,
                        "quality": "available",
                    },
                    {
                        "item_id": self.item.pk,
                        "quantity": "1",
                        "location_id": self.other_location.pk,
                        "quality": "available",
                    },
                ],
            },
        )
        self.assertEqual(bad.status_code, 404, bad.content)
        self.assertEqual(
            (InboundOrder.objects.count(), OperationRequest.objects.count()), before
        )
        self.assertEqual(self.stock(), 50)

    def test_purchase_supplier_and_integer_assets_required(self):
        row = {
            "item_id": self.fixed.pk,
            "quantity": "1.5",
            "location_id": self.location.pk,
            "quality": "available",
        }
        self.assertEqual(
            self.request(
                "post", "/api/v1/inbound/", {"type": "opening", "lines": [row]}
            ).status_code,
            422,
        )
        row["quantity"] = "1"
        self.assertEqual(
            self.request(
                "post", "/api/v1/inbound/", {"type": "purchase", "lines": [row]}
            ).status_code,
            422,
        )
        self.assertFalse(InboundOrder.objects.exists())

    def test_request_does_not_deduct_partial_execution_and_copy_remainder(self):
        self.receive()
        document = self.requisition(15)
        self.assertEqual(self.stock(), 50)
        self.assertEqual(document["status"], "pending")
        denied = self.execute(document, 10)
        self.assertEqual(denied.status_code, 409, denied.content)
        self.assertEqual(self.stock(), 50)
        response = self.execute(document, 10, reason="当前只发10包")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.stock(), 40)
        draft = self.request(
            "get", f"/api/v1/outbound/{document['id']}/copy/", user=self.user
        )
        self.assertEqual(draft.status_code, 200, draft.content)
        self.assertEqual(Decimal(draft.json()["draft"]["lines"][0]["quantity"]), 5)
        self.assertEqual(
            self.request("put", f"/api/v1/outbound/{document['id']}/", {}).status_code,
            405,
        )

    def test_zero_issuance_cancel_reason_and_frozen_terminal(self):
        document = self.requisition()
        self.assertEqual(self.execute(document, 0, reason="无库存").status_code, 409)
        self.assertEqual(
            self.act("outbound", document, "cancel", {}, self.user).status_code, 422
        )
        response = self.act(
            "outbound", document, "cancel", {"reason": "撤回申请"}, self.user
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.execute(document, 1).status_code, 409)
        self.assertEqual(
            OutboundItem.objects.filter(
                tenant_id=self.tenant.pk, order_id=document["id"]
            ).count(),
            1,
        )

    def test_execution_replay_different_key_and_invalid_line_identity(self):
        self.receive()
        document = self.requisition()
        line = OutboundItem.objects.get(
            tenant_id=self.tenant.pk, order_id=document["id"]
        )
        payload = {"lines": [{"id": line.pk, "actual_qty": "10"}]}
        key = str(uuid4())
        for _ in range(2):
            self.assertEqual(
                self.act("outbound", document, "execute", payload, key=key).status_code,
                200,
            )
        self.assertEqual(self.stock(), 40)
        self.assertEqual(
            self.act("outbound", document, "execute", payload).status_code, 409
        )
        self.assertEqual(
            StockLedger.objects.filter(outbound_item_id=line.pk).count(), 1
        )

    def test_insufficient_second_line_rolls_back_all_posting(self):
        self.receive()
        document = self.create(
            "outbound",
            "requisition",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "30",
                    "location_id": self.location.pk,
                },
                {
                    "item_id": self.item.pk,
                    "quantity": "30",
                    "location_id": self.location.pk,
                },
            ],
            self.user,
        )
        lines = list(
            OutboundItem.objects.filter(
                tenant_id=self.tenant.pk, order_id=document["id"]
            ).order_by("id")
        )
        response = self.act(
            "outbound",
            document,
            "execute",
            {"lines": [{"id": line.pk, "actual_qty": "30"} for line in lines]},
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(self.stock(), 50)
        self.assertFalse(
            StockLedger.objects.filter(
                outbound_item_id__in=[line.pk for line in lines]
            ).exists()
        )
        self.assertEqual(OutboundOrder.objects.get(pk=document["id"]).status, "pending")
        self.assertFalse(
            OperationRequest.objects.filter(response__isnull=True).exists()
        )

    def test_fixed_issue_count_return_and_event_state(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        document = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(document, 1).status_code, 409)
        response = self.execute(document, 1, [card.pk])
        self.assertEqual(response.status_code, 200, response.content)
        card.refresh_from_db()
        self.assertEqual(card.status, "in_use")
        relation = OutboundAsset.objects.get(
            tenant_id=self.tenant.pk, asset_card_id=card.pk
        )
        self.create(
            "inbound",
            "return",
            [
                {
                    "item_id": self.fixed.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "quality": "damaged",
                    "source_outbound_asset_id": relation.pk,
                }
            ],
        )
        card.refresh_from_db()
        relation.refresh_from_db()
        self.assertEqual(card.status, "repair")
        self.assertIsNone(card.responsible_user_id)
        self.assertEqual(relation.close_reason, "return")
        self.assertEqual(
            list(
                AssetEvent.objects.filter(asset_card_id=card.pk)
                .order_by("id")
                .values_list("action", flat=True)
            ),
            ["opening", "issue", "return"],
        )

    def test_consumable_return_cumulative_quota_and_atomic_rows(self):
        self.receive()
        document = self.requisition(10)
        self.assertEqual(self.execute(document, 10).status_code, 200)
        source = OutboundItem.objects.get(
            tenant_id=self.tenant.pk, order_id=document["id"]
        )
        row = {
            "item_id": self.item.pk,
            "quantity": "6",
            "location_id": self.location.pk,
            "quality": "available",
            "source_outbound_item_id": source.pk,
        }
        response = self.request(
            "post", "/api/v1/inbound/", {"type": "return", "lines": [row, row]}
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(self.stock(), 40)
        self.create("inbound", "return", [row])
        self.assertEqual(self.stock(), 46)
        self.assertEqual(
            self.request(
                "post", "/api/v1/inbound/", {"type": "return", "lines": [row]}
            ).status_code,
            409,
        )

    def test_purchase_return_source_type_and_quota(self):
        opening = self.receive()
        source = InboundItem.objects.get(
            tenant_id=self.tenant.pk, order_id=opening["id"]
        )
        row = {
            "item_id": self.item.pk,
            "quantity": "1",
            "location_id": self.location.pk,
            "source_inbound_item_id": source.pk,
        }
        self.assertEqual(
            self.request(
                "post", "/api/v1/outbound/", {"type": "purchase_return", "lines": [row]}
            ).status_code,
            409,
        )
        purchase = self.receive(5, purchase=True)
        source = InboundItem.objects.get(
            tenant_id=self.tenant.pk, order_id=purchase["id"]
        )
        row.update(source_inbound_item_id=source.pk, quantity="4")
        self.create("outbound", "purchase_return", [row])
        self.assertEqual(self.stock(), 51)
        self.assertEqual(
            self.request(
                "post", "/api/v1/outbound/", {"type": "purchase_return", "lines": [row]}
            ).status_code,
            409,
        )

    def test_scrap_self_approval_guard_and_terminal_freeze(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        document = self.create(
            "scrap",
            "scrap",
            [{"item_id": self.fixed.pk, "asset_card_id": card.pk}],
            reason="报废",
        )
        self.assertEqual(
            self.act("scrap", document, "approve", {}, self.admin).status_code, 409
        )
        response = self.act("scrap", document, "approve")
        self.assertEqual(response.status_code, 200, response.content)
        card.refresh_from_db()
        self.assertEqual(card.status, "scrapped")
        self.assertEqual(
            self.act("scrap", document, "cancel", {"reason": "不可修改"}).status_code,
            409,
        )

    def test_loss_closes_issue_and_found_never_revives_responsible_user(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        issued = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(issued, 1, [card.pk]).status_code, 200)
        document = self.create(
            "scrap",
            "loss",
            [{"item_id": self.fixed.pk, "asset_card_id": card.pk}],
            self.user,
            reason="遗失",
        )
        self.assertEqual(self.act("scrap", document, "approve").status_code, 200)
        line = ScrapItem.objects.get(tenant_id=self.tenant.pk, scrap_id=document["id"])
        relation = OutboundAsset.objects.get(
            tenant_id=self.tenant.pk, asset_card_id=card.pk
        )
        self.assertEqual(relation.close_reason, "loss")
        self.assertEqual(relation.loss_scrap_item_id, line.pk)
        data = {
            "item_id": self.fixed.pk,
            "asset_card_id": card.pk,
            "source_loss_item_id": line.pk,
            "recovery_location_id": self.location.pk,
            "recovery_quality": "available",
        }
        first = self.create("scrap", "recovery", [data], self.user, reason="找到")
        second = self.create("scrap", "recovery", [data], self.user, reason="重复找到")
        self.assertEqual(self.act("scrap", first, "approve").status_code, 200)
        self.assertEqual(self.act("scrap", second, "approve").status_code, 409)
        card.refresh_from_db()
        self.assertEqual(card.status, "in_stock")
        self.assertIsNone(card.responsible_user_id)
        self.assertEqual(
            AssetEvent.objects.filter(
                action="found", source_loss_item_id=line.pk
            ).count(),
            1,
        )

    def test_scrap_in_use_requires_actual_return_before_approval(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        issued = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(issued, 1, [card.pk]).status_code, 200)
        document = self.create(
            "scrap",
            "scrap",
            [{"item_id": self.fixed.pk, "asset_card_id": card.pk}],
            self.user,
            reason="报废申请",
        )
        self.assertEqual(self.act("scrap", document, "approve").status_code, 409)
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
        response = self.act("scrap", document, "approve")
        self.assertEqual(response.status_code, 200, response.content)

    def test_stock_damage_only_deducts_on_approval(self):
        self.receive()
        document = self.create(
            "scrap",
            "stock_damage",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "5",
                    "location_id": self.location.pk,
                    "quality": "available",
                }
            ],
            reason="库存受损",
        )
        self.assertEqual(self.stock(), 50)
        self.assertEqual(self.act("scrap", document, "approve").status_code, 200)
        self.assertEqual(self.stock(), 45)

    def test_count_adjustment_and_stale_same_quantity_new_version(self):
        self.receive()
        snapshot = self.snapshot()
        document = self.create(
            "adjustment",
            "count",
            [{**snapshot, "quantity": "2", "direction": "in", "counted_qty": "52"}],
            reason="盘盈",
            reason_category="gain",
        )
        self.receive(1)
        outbound = self.requisition(1)
        self.assertEqual(self.execute(outbound, 1).status_code, 200)
        self.assertEqual(self.stock(), 50)
        response = self.act("adjustment", document, "approve")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "STALE_SNAPSHOT")
        self.assertEqual(
            AdjustmentOrder.objects.get(pk=document["id"]).status, "pending"
        )
        fresh = self.create(
            "adjustment",
            "count",
            [
                {
                    **self.snapshot(),
                    "quantity": "2",
                    "direction": "in",
                    "counted_qty": "52",
                }
            ],
            reason="重新盘盈",
            reason_category="gain",
        )
        self.assertEqual(self.act("adjustment", fresh, "approve").status_code, 200)
        self.assertEqual(self.stock(), 52)

    def test_quality_conversion_has_equal_opposite_ledger_and_no_total_change(self):
        self.receive()
        data = {
            **self.snapshot(),
            "quantity": "12",
            "direction": "out",
            "to_quality": "damaged",
            "target_inventory_version": 0,
        }
        document = self.create(
            "adjustment",
            "quality_transfer",
            [data],
            reason="质量转换",
            reason_category="quality_change",
        )
        response = self.act("adjustment", document, "approve")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((self.stock(), self.stock("damaged")), (38, 12))
        line = AdjustmentItem.objects.get(
            tenant_id=self.tenant.pk, order_id=document["id"]
        )
        self.assertEqual(
            set(
                StockLedger.objects.filter(adjustment_item_id=line.pk).values_list(
                    "direction", "quantity"
                )
            ),
            {("in", Decimal(12)), ("out", Decimal(12))},
        )

    def test_fixed_gain_creates_adjustment_inbound_card_and_event(self):
        document = self.create(
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
                    "original_value": "300.00",
                }
            ],
            reason="资产盘盈",
            reason_category="gain",
        )
        response = self.act("adjustment", document, "approve")
        self.assertEqual(response.status_code, 200, response.content)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        source = InboundItem.objects.get(pk=card.source_inbound_item_id)
        self.assertEqual(
            InboundOrder.objects.get(pk=source.order_id).type, "adjustment_gain"
        )
        self.assertEqual(card.original_value, 300)
        self.assertTrue(
            AssetEvent.objects.filter(
                action="found_gain", adjustment_item_id=source.adjustment_item_id
            ).exists()
        )

    def test_reject_cancel_decisions_and_ownership_isolation(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        document = self.create(
            "scrap",
            "loss",
            [{"item_id": self.fixed.pk, "asset_card_id": card.pk}],
            reason="申请",
        )
        self.assertEqual(self.act("scrap", document, "reject", {}).status_code, 422)
        self.assertEqual(
            self.act("scrap", document, "reject", {"reason": "资料待补充"}).status_code,
            200,
        )
        header = ScrapOrder.objects.get(pk=document["id"])
        self.assertEqual(header.decided_by, self.approver.pk)
        self.assertTrue(header.decided_at)
        self.assertEqual(
            self.request(
                "get", f"/api/v1/scrap/{header.pk}/", user=self.other
            ).status_code,
            404,
        )
        self.assertEqual(
            self.request("get", "/api/v1/inbound/", user=self.user).status_code, 403
        )

    def test_transfer_is_paired_return_issue_without_third_event(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        issued = self.requisition(1, fixed=True)
        self.assertEqual(self.execute(issued, 1, [card.pk]).status_code, 200)
        relation = OutboundAsset.objects.get(
            tenant_id=self.tenant.pk, asset_card_id=card.pk
        )
        transfer = self.create(
            "outbound",
            "transfer",
            [
                {
                    "item_id": self.fixed.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "recipient_id": self.approver.pk,
                    "source_outbound_asset_id": relation.pk,
                }
            ],
        )
        self.assertEqual(transfer["status"], "completed")
        relation.refresh_from_db()
        self.assertEqual(relation.close_reason, "transfer")
        card.refresh_from_db()
        self.assertEqual(card.responsible_user_id, self.approver.pk)
        self.assertEqual(
            list(
                AssetEvent.objects.filter(
                    tenant_id=self.tenant.pk, asset_card_id=card.pk
                )
                .order_by("id")
                .values_list("action", flat=True)
            ),
            ["opening", "issue", "return", "issue"],
        )
        self.assertEqual(
            InboundOrder.objects.filter(
                tenant_id=self.tenant.pk, transfer_id=transfer["transfer_id"]
            ).count(),
            1,
        )

    def test_unknown_frozen_fields_and_duplicate_snapshot_rejected(self):
        response = self.request(
            "post",
            "/api/v1/outbound/",
            {
                "type": "requisition",
                "status": "completed",
                "applicant_id": self.admin.pk,
                "lines": [{"item_id": self.item.pk, "quantity": "1"}],
            },
            self.user,
        )
        self.assertEqual(response.status_code, 422)
        self.receive()
        data = {
            **self.snapshot(),
            "quantity": "1",
            "direction": "in",
            "counted_qty": "51",
        }
        self.assertEqual(
            self.request(
                "post",
                "/api/v1/adjustment/",
                {
                    "type": "count",
                    "reason": "重复快照",
                    "reason_category": "gain",
                    "lines": [data, data],
                },
            ).status_code,
            422,
        )

    def test_html_pages_and_htmx_create_requisition(self):
        token = access_token(authorize_claims(self.claims[self.user.pk]))
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token}
        page = self.client.get("/outbound/new/", **headers)
        self.assertEqual(page.status_code, 200, page.content)
        self.assertContains(page, 'name="lines-__prefix__-item_id"')
        self.assertContains(
            page, f'<option value="{self.item.pk}">复印纸</option>', count=2
        )
        data = {
            "type": "requisition",
            "idempotency_key": str(uuid4()),
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-MAX_NUM_FORMS": "50",
            "lines-0-item_id": str(self.item.pk),
            "lines-0-quantity": "2",
            "lines-0-location_id": str(self.location.pk),
        }
        response = self.client.post(
            "/outbound/new/", data, HTTP_HX_REQUEST="true", **headers
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("HX-Redirect", response)
        detail = self.client.get(response["HX-Redirect"], **headers)
        self.assertContains(detail, "原申请内容只读")

    def test_malformed_execution_identifiers_return_validation_error(self):
        document = self.requisition()
        line = OutboundItem.objects.get(order_id=document["id"])
        for field, value in [("recipient_id", "invalid"), ("asset_ids", ["invalid"])]:
            with self.subTest(field=field):
                response = self.act(
                    "outbound",
                    document,
                    "execute",
                    {"lines": [{"id": line.pk, "actual_qty": "1", field: value}]},
                )
                self.assertEqual(response.status_code, 422, response.content)
        self.assertEqual(OutboundOrder.objects.get(pk=document["id"]).status, "pending")

    def test_fixed_purchase_return_matches_original_card_and_closes_relation(self):
        self.receive(1, fixed=True)
        wrong_card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        purchase = self.receive(1, fixed=True, purchase=True)
        source = InboundItem.objects.get(order_id=purchase["id"])
        card = AssetCard.objects.get(source_inbound_item_id=source.pk)
        row = {
            "item_id": self.fixed.pk,
            "quantity": "1",
            "location_id": self.location.pk,
            "source_inbound_item_id": source.pk,
            "asset_ids": [wrong_card.pk],
        }
        response = self.request(
            "post", "/api/v1/outbound/", {"type": "purchase_return", "lines": [row]}
        )
        self.assertEqual(response.status_code, 409, response.content)
        row["asset_ids"] = [card.pk]
        self.create("outbound", "purchase_return", [row])
        card.refresh_from_db()
        relation = OutboundAsset.objects.get(asset_card_id=card.pk)
        self.assertEqual(card.status, "returned_to_supplier")
        self.assertIsNone(card.responsible_user_id)
        self.assertEqual(relation.close_reason, "purchase_return")
        self.assertIsNotNone(relation.closed_at)
        self.assertFalse(Inventory.objects.filter(item_id=self.fixed.pk).exists())

    def test_consumable_transfer_keeps_total_stock_and_return_quota(self):
        self.receive()
        issued = self.requisition()
        self.assertEqual(self.execute(issued, 10).status_code, 200)
        source = OutboundItem.objects.get(order_id=issued["id"])
        row = {
            "item_id": self.item.pk,
            "quantity": "6",
            "location_id": self.location.pk,
            "source_outbound_item_id": source.pk,
            "recipient_id": self.approver.pk,
        }
        document = self.create("outbound", "transfer", [row])
        self.assertEqual(document["status"], "completed")
        self.assertEqual(self.stock(), 40)
        transfer_line = OutboundItem.objects.get(order_id=document["id"])
        self.assertEqual(transfer_line.actual_qty, 6)
        response = self.request(
            "post", "/api/v1/outbound/", {"type": "transfer", "lines": [row]}
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(self.stock(), 40)

    def test_transfer_retry_discards_rolled_back_source_ids(self):
        from apps.inventory import posting

        self.receive()
        issued = self.requisition()
        self.assertEqual(self.execute(issued, 10).status_code, 200)
        source = OutboundItem.objects.get(order_id=issued["id"])
        receive = posting.receive
        calls = []

        def first_attempt_conflict(*args, **kwargs):
            receive(*args, **kwargs)
            calls.append(True)
            if len(calls) == 1:
                raise OperationalError(
                    "simulated deadlock"
                ) from psycopg.errors.DeadlockDetected()

        with patch.object(posting, "receive", side_effect=first_attempt_conflict):
            document = self.create(
                "outbound",
                "transfer",
                [
                    {
                        "item_id": self.item.pk,
                        "quantity": "5",
                        "location_id": self.location.pk,
                        "source_outbound_item_id": source.pk,
                        "recipient_id": self.approver.pk,
                    }
                ],
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.stock(), 40)
        self.assertEqual(
            InboundOrder.objects.filter(transfer_id=document["transfer_id"]).count(), 1
        )
        self.assertEqual(StockLedger.objects.count(), 4)

    def test_self_approval_exception_requires_reason_and_records_snapshot(self):
        from apps.core import services as core

        p = authorize_claims(self.claims[self.admin.pk])
        core.save(
            p,
            "tenant",
            self.tenant.pk,
            str(uuid4()),
            {
                "name": self.tenant.name,
                "asset_prefix": self.tenant.asset_prefix,
                "login_failure_limit": self.tenant.login_failure_limit,
                "login_lock_minutes": self.tenant.login_lock_minutes,
                "allow_self_approval": True,
                "reason": "单人值班例外",
            },
        )
        self.receive()
        document = self.create(
            "scrap",
            "stock_damage",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "2",
                    "location_id": self.location.pk,
                    "quality": "available",
                }
            ],
            reason="库存损坏",
        )
        self.assertEqual(
            self.act("scrap", document, "approve", {}, self.admin).status_code, 409
        )
        response = self.act(
            "scrap", document, "approve", {"reason": "单人值班核实"}, self.admin
        )
        self.assertEqual(response.status_code, 200, response.content)
        header = ScrapOrder.objects.get(pk=document["id"])
        self.assertTrue(header.self_approved)
        self.assertTrue(header.allow_self_approval_snapshot)

    def test_quality_transfer_rejects_changed_target_version(self):
        self.receive()
        document = self.create(
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
            reason="质量变化",
            reason_category="quality_change",
        )
        self.create(
            "inbound",
            "opening",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "1",
                    "location_id": self.location.pk,
                    "quality": "damaged",
                }
            ],
        )
        response = self.act("adjustment", document, "approve")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "STALE_SNAPSHOT")
        self.assertEqual((self.stock(), self.stock("damaged")), (50, 1))
