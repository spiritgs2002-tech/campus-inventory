from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase
from shared.auth.backends import authorize_claims
from shared.errors import BusinessError
from apps.procurement import services
from apps.procurement.models import Requisition
from apps.notifications.models import Notification
from apps.core.models import User
from apps.inventory.qr import generate_item_tokens
from .order_fixtures import OrderFixtureMixin


class RequisitionTests(OrderFixtureMixin, TestCase):
    def principal(self, user=None):
        return authorize_claims(self.claims[(user or self.admin).pk])

    def submit(self, key=None, **changes):
        data = {"item_id": self.item.pk, "qty": "2", "reason": "教学使用"}
        data.update(changes)
        return services.requisition_submit(
            self.principal(self.user), data, key or uuid4().hex
        )

    def approved(self):
        result, _ = self.submit()
        pk = result["document"]["id"]
        services.decide(self.principal(), pk, "approve", uuid4().hex)
        return pk

    def fulfill(self, pk, key=None):
        return services.fulfill(
            self.principal(),
            pk,
            key or uuid4().hex,
            self.location.pk,
            "available",
            self.supplier.pk,
            "3.50",
        )

    def test_submit_records_trusted_identity(self):
        result, _ = self.submit(name="伪造")
        self.assertEqual(result["document"]["applicant_name"], self.user.name)

    def test_submit_api(self):
        response = self.request(
            "post",
            "/api/v1/requisition/",
            {"item_id": self.item.pk, "qty": "1", "reason": "教学"},
            user=self.user,
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_replay(self):
        key = uuid4().hex
        first, _ = self.submit(key)
        again, replay = self.submit(key, qty="2.000")
        self.assertTrue(replay)
        self.assertEqual(first, again)

    def test_digest_includes_reason(self):
        key = uuid4().hex
        self.submit(key)
        with self.assertRaises(BusinessError):
            self.submit(key, reason="changed")

    def test_invalid_quantity(self):
        for q in ["0", "-1", "NaN", "1.0001"]:
            with self.assertRaises(BusinessError):
                self.submit(qty=q)

    def test_unknown_fields_rejected(self):
        with self.assertRaises(BusinessError):
            self.submit(tenant_id=self.other_tenant.pk)

    def test_cross_tenant_item(self):
        with self.assertRaises(BusinessError):
            services.requisition_submit(
                self.principal(self.other),
                {"item_id": self.item.pk, "qty": "1", "reason": "x"},
                uuid4().hex,
            )

    def test_approve(self):
        self.assertEqual(Requisition.objects.get(pk=self.approved()).status, "approved")

    def test_reject_notification(self):
        r, _ = self.submit()
        services.decide(
            self.principal(), r["document"]["id"], "reject", uuid4().hex, "预算不足"
        )
        self.assertEqual(Notification.objects.get().type, "requisition_rejected")

    def test_reject_reason_required(self):
        r, _ = self.submit()
        with self.assertRaises(BusinessError):
            services.decide(
                self.principal(), r["document"]["id"], "reject", uuid4().hex
            )

    def test_teacher_cannot_approve(self):
        r, _ = self.submit()
        with self.assertRaises(BusinessError):
            services.decide(
                self.principal(self.user), r["document"]["id"], "approve", uuid4().hex
            )

    def test_fulfill_stock_and_notification(self):
        pk = self.approved()
        self.fulfill(pk)
        self.assertEqual(self.stock(), 2)
        self.assertEqual(Requisition.objects.get(pk=pk).status, "purchased")
        self.assertEqual(Notification.objects.get().recipient_user_id, self.user.pk)

    def test_fulfill_replay(self):
        pk = self.approved()
        key = uuid4().hex
        self.fulfill(pk, key)
        _, replay = self.fulfill(pk, key)
        self.assertTrue(replay)
        self.assertEqual(self.stock(), 2)
        self.assertEqual(Notification.objects.count(), 1)

    def test_different_key_cannot_fulfill_twice(self):
        pk = self.approved()
        self.fulfill(pk)
        with self.assertRaises(BusinessError):
            self.fulfill(pk)

    def test_missing_supplier_rolls_back(self):
        pk = self.approved()
        with self.assertRaises(BusinessError):
            services.fulfill(self.principal(), pk, uuid4().hex, self.location.pk)
        self.assertEqual(self.stock(), 0)
        self.assertEqual(Requisition.objects.get(pk=pk).status, "approved")

    def test_notification_failure_rolls_back_inbound(self):
        pk = self.approved()
        with patch(
            "apps.procurement.services.notify_arrival",
            side_effect=RuntimeError("injected"),
        ):
            with self.assertRaises(RuntimeError):
                self.fulfill(pk)
        self.assertEqual(self.stock(), 0)
        self.assertEqual(Requisition.objects.get(pk=pk).status, "approved")

    def test_list_is_scoped(self):
        self.submit()
        self.assertEqual(services.visible(self.principal(self.user)).count(), 1)
        self.assertEqual(services.visible(self.principal(self.other)).count(), 0)

    def test_html_pages(self):
        self.submit()
        for url in ["/s/requisition/new/", "/s/requisitions/", "/manage/requisitions/"]:
            response = self.request("get", url)
            self.assertEqual(response.status_code, 200, response.content)

    def test_anonymous_reuses_trace_identity(self):
        generate_item_tokens(self.principal(), uuid4().hex)
        self.item.refresh_from_db()
        data = {
            "item_id": self.item.pk,
            "qty": "1",
            "reason": "教学",
            "name": "匿名教师",
            "employee_no": "ANON",
            "token": self.item.qr_token,
        }
        for _ in range(2):
            services.requisition_submit(None, data, uuid4().hex)
        self.assertEqual(
            User.objects.filter(tenant_id=self.tenant.pk, username="ANON").count(), 1
        )

    def test_anonymous_formal_identity_rejected(self):
        generate_item_tokens(self.principal(), uuid4().hex)
        self.item.refresh_from_db()
        with self.assertRaises(BusinessError):
            services.requisition_submit(
                None,
                {
                    "item_id": self.item.pk,
                    "qty": "1",
                    "reason": "x",
                    "name": self.user.name,
                    "employee_no": self.user.username,
                    "token": self.item.qr_token,
                },
                uuid4().hex,
            )

    def test_anonymous_json_requires_csrf(self):
        from django.test import Client

        response = Client(enforce_csrf_checks=True).post(
            "/api/v1/requisition/", data="{}", content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)

    def test_fulfillment_remains_reconciled(self):
        from apps.reconcile.services import collect
        from django.db import connection

        self.fulfill(self.approved())
        with connection.cursor() as cursor:
            self.assertTrue(collect(cursor, 20)["ok"])
