from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
import json
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from apps.catalog.models import Category, Item, Location
from apps.core.models import AuthSession, AuditLog, OperationRequest, Tenant, User
from apps.inventory import pickup, posting
from apps.inventory.models import (
    AssetCard,
    AssetEvent,
    Inventory,
    OutboundAsset,
    OutboundItem,
    OutboundOrder,
    StockLedger,
)
from shared.auth.backends import authorize_claims
from shared.auth.passwords import verify_password
from shared.auth.tokens import access_token
from shared.errors import BusinessError
from .order_fixtures import OrderFixtureMixin


class PickupTests(OrderFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.unavailable_tokens = []
        category = Category.objects.create(
            tenant_id=cls.other_tenant.pk,
            code="FOREIGN",
            name="外校",
            type="consumable",
        )
        cls.foreign_token = uuid4().hex * 2
        Item.objects.create(
            tenant_id=cls.other_tenant.pk,
            category_id=category.pk,
            code="FOREIGN",
            name="外校用品",
            type="consumable",
            unit="包",
            qr_token=cls.foreign_token,
        )
        for code, fields in [
            ("EXPIRED", {"expire_at": timezone.localdate() - timedelta(days=1)}),
            ("DISABLED", {"status": "disabled"}),
        ]:
            tenant = Tenant.objects.create(code=code, name=code, **fields)
            category = Category.objects.create(
                tenant_id=tenant.pk, code="C", name="C", type="consumable"
            )
            token = uuid4().hex * 2
            Item.objects.create(
                tenant_id=tenant.pk,
                category_id=category.pk,
                code="C",
                name="不可用学校物资",
                type="consumable",
                unit="包",
                qr_token=token,
            )
            cls.unavailable_tokens.append(token)

    def setUp(self):
        super().setUp()
        self.item.qr_token = uuid4().hex + uuid4().hex
        self.item.save(update_fields=["qr_token"])

    def card(self):
        self.receive(quantity=1, fixed=True)
        return AssetCard.objects.filter(tenant_id=self.tenant.pk).order_by("id").last()

    def anonymous(self, token=None, quantity="1", key=None, **kwargs):
        return self.client.post(
            f"/api/v1/pickup/{token or self.item.qr_token}/",
            json.dumps(
                {
                    "name": "扫码教师",
                    "employee_no": "T-001",
                    "quantity": quantity,
                    **kwargs,
                }
            ),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key or uuid4().hex,
        )

    def test_v_scan_1_public_item_and_asset_show_minimal_metadata(self):
        card = self.card()
        for token, name in [
            (self.item.qr_token, self.item.name),
            (card.qr_token, self.fixed.name),
        ]:
            response = self.client.get(f"/s/pickup/{token}/")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertContains(response, name)
            self.assertContains(response, self.tenant.name)
            self.assertContains(response, "csrfmiddlewaretoken")
            self.assertContains(response, "未登录")
            self.assertNotContains(response, "original_value")
            self.assertNotContains(response, self.approver.name)

    def test_v_scan_2_logged_user_asset_issued_to_self_with_event(self):
        card = self.card()
        response = self.request(
            "post", f"/api/v1/pickup/{card.qr_token}/", {}, self.user
        )
        self.assertEqual(response.status_code, 201, response.content)
        header = OutboundOrder.objects.get(pk=response.json()["document"]["id"])
        line = OutboundItem.objects.get(order_id=header.pk)
        card.refresh_from_db()
        self.assertEqual(
            (header.type, header.status, header.applicant_id, header.operator_id),
            ("requisition", "completed", self.user.pk, self.user.pk),
        )
        self.assertTrue(header.self_issued)
        self.assertEqual(line.recipient_id, self.user.pk)
        self.assertEqual(
            (card.status, card.responsible_user_id), ("in_use", self.user.pk)
        )
        self.assertEqual(
            OutboundAsset.objects.filter(
                asset_card_id=card.pk, closed_at__isnull=True
            ).count(),
            1,
        )
        self.assertTrue(
            AssetEvent.objects.filter(
                asset_card_id=card.pk, action="issue", operation_id=header.operation_id
            ).exists()
        )

    def test_v_scan_3_anonymous_consumable_creates_unusable_trace_identity_only(self):
        self.receive(5)
        response = self.anonymous(quantity="2")
        self.assertEqual(response.status_code, 201, response.content)
        user = User.objects.get(tenant_id=self.tenant.pk, username="T-001")
        self.assertEqual(user.role, "user")
        self.assertTrue(user.password_hash.startswith(pickup.UNVERIFIED))
        self.assertFalse(verify_password("anything-at-all", user.password_hash))
        self.assertFalse(AuthSession.objects.filter(user_id=user.pk).exists())
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        header = OutboundOrder.objects.get(pk=response.json()["document"]["id"])
        self.assertEqual(header.applicant_id, user.pk)
        self.assertEqual(
            OutboundItem.objects.get(order_id=header.pk).recipient_id, user.pk
        )
        self.assertEqual(self.stock(), Decimal(3))
        self.assertEqual(response.json()["pickup"]["identity"], "unverified")
        audit = AuditLog.objects.get(object_type="outbound", object_id=header.pk)
        self.assertEqual(
            audit.changes["after"]["claimed_identity"],
            {"name": "扫码教师", "employee_no": "T-001", "verified": False},
        )
        self.assertNotIn("access_token", response.json())
        self.assertEqual(self.client.get("/api/v1/stock/").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/v1/outbound/", "{}", content_type="application/json"
            ).status_code,
            401,
        )

    def test_anonymous_asset_uses_same_posting_and_self_relation(self):
        card = self.card()
        result = self.anonymous(card.qr_token)
        self.assertEqual(result.status_code, 201, result.content)
        card.refresh_from_db()
        self.assertEqual(
            card.responsible_user_id, User.objects.get(username="T-001").pk
        )
        self.assertEqual(card.status, "in_use")

    def test_same_key_replay_and_payload_conflict(self):
        self.receive(5)
        key = uuid4().hex
        first = self.anonymous(quantity="2", key=key)
        second = self.anonymous(quantity="2.000", key=key)
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second["Idempotency-Replayed"], "true")
        self.assertEqual(self.anonymous(quantity="1", key=key).status_code, 409)
        self.assertEqual(OutboundOrder.objects.count(), 1)
        self.assertEqual(StockLedger.objects.filter(direction="out").count(), 1)

    def test_same_key_cannot_change_scanned_item(self):
        self.receive(4)
        second = Item.objects.create(
            tenant_id=self.tenant.pk,
            category_id=self.item.category_id,
            code="OTHER",
            name="另一物品",
            type="consumable",
            unit="包",
            qr_token=uuid4().hex * 2,
        )
        key = uuid4().hex
        self.assertEqual(self.anonymous(key=key).status_code, 201)
        result = self.anonymous(second.qr_token, key=key)
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(result.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_formal_admin_or_user_cannot_be_impersonated_by_employee_number(self):
        self.receive(4)
        for user in [self.admin, self.user]:
            result = self.anonymous(name=user.name, employee_no=user.username)
            self.assertEqual(result.status_code, 409, result.content)
            self.assertEqual(
                result.json()["error"]["code"], "LOGIN_REQUIRED_FOR_EMPLOYEE"
            )
        self.assertEqual(OutboundOrder.objects.count(), 0)
        self.assertEqual(self.stock(), Decimal(4))

    def test_disabled_or_renamed_lightweight_user_cannot_be_reactivated(self):
        self.receive(4)
        self.assertEqual(self.anonymous().status_code, 201)
        user = User.objects.get(username="T-001")
        changed = self.anonymous(name="不同姓名")
        self.assertEqual(changed.status_code, 409)
        User.objects.filter(pk=user.pk).update(active=False)
        self.assertEqual(self.anonymous().status_code, 409)
        user.refresh_from_db()
        self.assertFalse(user.active)
        self.assertEqual(OutboundOrder.objects.count(), 1)

    def test_extra_recipient_item_tenant_or_asset_fields_are_rejected(self):
        self.receive(4)
        for field in [
            "recipient_id",
            "item_id",
            "tenant_id",
            "asset_ids",
            "location_id",
        ]:
            result = self.anonymous(**{field: self.other.pk})
            self.assertEqual(result.status_code, 422, result.content)
        self.assertFalse(User.objects.filter(username="T-001").exists())
        self.assertFalse(OutboundOrder.objects.exists())

    def test_logged_in_user_cannot_scan_other_school_or_downgrade_to_anonymous(self):
        self.receive(4)
        result = self.request(
            "post",
            f"/api/v1/pickup/{self.item.qr_token}/",
            {"name": "X", "employee_no": "X", "quantity": "1"},
            self.other,
        )
        self.assertEqual(result.status_code, 404, result.content)
        invalid = self.client.post(
            f"/api/v1/pickup/{self.item.qr_token}/",
            json.dumps({"name": "X", "employee_no": "X", "quantity": "1"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid",
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
        )
        self.assertEqual(invalid.status_code, 401, invalid.content)
        self.assertFalse(User.objects.filter(username="X").exists())

    def test_forced_change_and_revoked_family_are_not_anonymous(self):
        token = access_token(authorize_claims(self.claims[self.user.pk]))
        User.objects.filter(pk=self.user.pk).update(must_change_password=True)
        public_page = self.client.get(
            f"/s/pickup/{self.item.qr_token}/", HTTP_AUTHORIZATION="Bearer " + token
        )
        self.assertContains(public_page, self.item.name)
        self.assertContains(public_page, "当前登录状态不允许提交领用")
        self.assertNotContains(public_page, 'name="employee_no"')
        self.assertEqual(
            self.client.get(
                f"/s/pickup/{self.foreign_token}/", HTTP_AUTHORIZATION="Bearer " + token
            ).status_code,
            404,
        )
        path = f"/api/v1/pickup/{self.item.qr_token}/"
        data = json.dumps({"quantity": "1", "name": "X", "employee_no": "X"})
        response = self.client.post(
            path,
            data,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
        )
        self.assertEqual(response.status_code, 403, response.content)
        AuthSession.objects.filter(user_id=self.user.pk).update(
            revoked_at=timezone.now()
        )
        response = self.client.post(
            path,
            data,
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
        )
        self.assertEqual(response.status_code, 401, response.content)
        self.assertFalse(User.objects.filter(username="X").exists())

    def test_disabled_tenant_and_invalid_or_ambiguous_tokens_fail_closed(self):
        for token in ["unknown", uuid4().hex * 2]:
            self.assertEqual(self.client.get(f"/s/pickup/{token}/").status_code, 404)
        # Cross-kind ambiguity is possible with manual legacy data; never select first.
        card = self.card()
        self.item.qr_token = card.qr_token
        self.item.save(update_fields=["qr_token"])
        self.assertEqual(
            self.client.get(f"/s/pickup/{card.qr_token}/").status_code, 404
        )
        for token in self.unavailable_tokens:
            self.assertEqual(self.client.get(f"/s/pickup/{token}/").status_code, 404)

    def test_unavailable_asset_cannot_be_issued_twice(self):
        card = self.card()
        self.assertEqual(self.anonymous(card.qr_token).status_code, 201)
        rejected = self.anonymous(card.qr_token, employee_no="T-002")
        self.assertEqual(rejected.status_code, 409, rejected.content)
        self.assertFalse(User.objects.filter(username="T-002").exists())
        self.assertEqual(OutboundAsset.objects.count(), 1)

    def test_shortage_or_invalid_quantity_rolls_back_lightweight_user_and_key(self):
        self.receive(2)
        for value in ["0", "-1", "NaN", "1.0001", "99999999999999", "3"]:
            result = self.anonymous(quantity=value)
            self.assertIn(result.status_code, [422, 409], result.content)
        self.assertFalse(User.objects.filter(username="T-001").exists())
        self.assertFalse(
            OperationRequest.objects.filter(operation=pickup.OPERATION).exists()
        )
        self.assertEqual(self.stock(), Decimal(2))

    def test_stock_is_allocated_across_active_locations_in_order(self):
        self.receive(2)
        second = Location.objects.create(tenant_id=self.tenant.pk, name="第二库")
        self.create(
            "inbound",
            "opening",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "3",
                    "location_id": second.pk,
                    "quality": "available",
                    "unit_price": "1",
                }
            ],
        )
        response = self.anonymous(quantity="4")
        self.assertEqual(response.status_code, 201, response.content)
        lines = list(OutboundItem.objects.order_by("location_id"))
        self.assertEqual([line.actual_qty for line in lines], [Decimal(2), Decimal(2)])
        self.assertEqual(
            [row["quantity"] for row in response.json()["pickup"]["allocations"]],
            ["2", "2"],
        )
        self.assertEqual(
            [row["location"] for row in response.json()["pickup"]["allocations"]],
            [self.location.name, second.name],
        )
        self.assertEqual(
            sum(Inventory.objects.values_list("quantity", flat=True)), Decimal(1)
        )

    def test_damaged_or_disabled_locations_are_not_available(self):
        self.create(
            "inbound",
            "opening",
            [
                {
                    "item_id": self.item.pk,
                    "quantity": "5",
                    "location_id": self.location.pk,
                    "quality": "damaged",
                    "unit_price": "1",
                }
            ],
        )
        self.assertEqual(self.anonymous().status_code, 409)
        self.receive(2)
        Location.objects.filter(pk=self.location.pk).update(active=False)
        self.assertEqual(self.anonymous().status_code, 409)
        self.assertFalse(OutboundOrder.objects.exists())

    def test_posting_failure_rolls_back_all_business_and_identity_rows(self):
        self.receive(3)
        original = posting.issue

        def failing(*args):
            original(*args)
            raise BusinessError("INJECTED", "injected after posting", 409)

        with patch("apps.inventory.pickup.posting.issue", side_effect=failing):
            result = self.anonymous()
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(self.stock(), Decimal(3))
        self.assertFalse(OutboundOrder.objects.exists())
        self.assertFalse(User.objects.filter(username="T-001").exists())
        self.assertFalse(StockLedger.objects.filter(direction="out").exists())
        self.assertFalse(AuditLog.objects.filter(object_type="outbound").exists())

    def test_anonymous_rate_budget_does_not_block_idempotent_replay(self):
        self.receive(4)
        with override_settings(PICKUP_ANONYMOUS_PER_MINUTE=1):
            key = uuid4().hex
            self.assertEqual(self.anonymous(key=key).status_code, 201)
            self.assertEqual(self.anonymous(key=key).status_code, 200)
            self.assertEqual(self.anonymous(employee_no="T-002").status_code, 429)
        self.assertFalse(User.objects.filter(username="T-002").exists())

    def test_anonymous_json_csrf_and_server_form_work_without_javascript(self):
        self.receive(3)
        client = Client(enforce_csrf_checks=True)
        path = f"/api/v1/pickup/{self.item.qr_token}/"
        payload = {"quantity": "1", "name": "表单教师", "employee_no": "FORM-1"}
        self.assertEqual(
            client.post(
                path,
                json.dumps(payload),
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=uuid4().hex,
            ).status_code,
            403,
        )
        page = client.get(f"/s/pickup/{self.item.qr_token}/")
        csrf = page.cookies["csrftoken"].value
        accepted = client.post(
            path,
            json.dumps(payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=uuid4().hex,
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(accepted.status_code, 201, accepted.content)
        key = uuid4().hex
        form = client.post(
            f"/s/pickup/{self.item.qr_token}/",
            {**payload, "idempotency_key": key, "csrfmiddlewaretoken": csrf},
        )
        self.assertContains(form, "领用成功", status_code=200)
        repeated = client.post(
            f"/s/pickup/{self.item.qr_token}/",
            {**payload, "idempotency_key": key, "csrfmiddlewaretoken": csrf},
        )
        self.assertContains(repeated, "没有重复出库")
        self.assertEqual(self.stock(), Decimal(1))

    def test_api_media_method_and_identity_validation(self):
        path = f"/api/v1/pickup/{self.item.qr_token}/"
        self.assertEqual(self.client.get(path).status_code, 405)
        self.assertEqual(self.client.post(path, {}).status_code, 415)
        for body in [
            "{",
            "[]",
            json.dumps({"quantity": "1"}),
            json.dumps({"quantity": "1", "name": [], "employee_no": "A"}),
        ]:
            result = self.client.post(
                path,
                body,
                content_type="application/json",
                HTTP_IDEMPOTENCY_KEY=uuid4().hex,
            )
            self.assertEqual(result.status_code, 422, result.content)

    def test_v_inv_1_mobile_search_uses_existing_user_authorization(self):
        self.receive(4)
        mobile = self.request("get", "/s/inventory/", {"q": "复印纸"}, self.user)
        self.assertContains(mobile, self.item.name)
        self.assertNotContains(mobile, "4.000")
        self.assertNotContains(mobile, "version")
        no_results = self.request("get", "/s/inventory/", {"q": "没有这个"}, self.user)
        self.assertNotContains(no_results, self.item.name)
        self.assertEqual(self.client.get("/s/inventory/").status_code, 302)
        self.assertEqual(
            self.request(
                "get", "/s/inventory/", {"stock_page": "bad"}, self.user
            ).status_code,
            422,
        )

    def test_mobile_assets_only_show_current_users_cards_and_placeholder_works(self):
        own = self.card()
        self.request("post", f"/api/v1/pickup/{own.qr_token}/", {}, self.user)
        other = self.card()
        self.request("post", f"/api/v1/pickup/{other.qr_token}/", {}, self.admin)
        page = self.request("get", "/s/inventory/", {}, self.user)
        self.assertContains(page, own.asset_no)
        self.assertNotContains(page, other.asset_no)
        self.assertEqual(
            self.request("get", "/s/requisition/new/", {}, self.user).status_code, 200
        )

    def test_direct_service_rejects_delegation_and_forged_lightweight_principal(self):
        p = authorize_claims(self.claims[self.user.pk])
        with self.assertRaises(BusinessError):
            pickup.pickup_issue(p, self.item.qr_token, 1, self.admin.pk, uuid4().hex)
        fake = pickup.AnonymousPickup(
            self.tenant.pk, self.admin.pk, self.admin.username, self.admin.name
        )
        with self.assertRaises(BusinessError):
            pickup.pickup_issue(fake, self.item.qr_token, 1, self.admin.pk, uuid4().hex)
