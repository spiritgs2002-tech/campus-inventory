import json
from decimal import Decimal
from uuid import uuid4
from datetime import timedelta
from django.db import connection
from django.utils import timezone
from apps.core.models import Tenant, User, AuthSession
from apps.catalog.models import Category, Item, Location, Supplier
from apps.inventory.models import (
    OutboundItem,
    Inventory,
)
from shared.auth.backends import authorize_claims
from shared.auth.tokens import access_token


class OrderFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="ORDERS", name="单据测试校")
        cls.other_tenant = Tenant.objects.create(code="OUTSIDE", name="其他学校")
        cls.admin = User.objects.create(
            tenant_id=cls.tenant.pk,
            username="a",
            name="管理员甲",
            password_hash="fixture-unused",
            role="admin",
            must_change_password=False,
        )
        cls.approver = User.objects.create(
            tenant_id=cls.tenant.pk,
            username="b",
            name="管理员乙",
            password_hash="fixture-unused",
            role="admin",
            must_change_password=False,
        )
        cls.user = User.objects.create(
            tenant_id=cls.tenant.pk,
            username="u",
            name="领用人",
            password_hash="fixture-unused",
            role="user",
            must_change_password=False,
        )
        cls.other = User.objects.create(
            tenant_id=cls.other_tenant.pk,
            username="o",
            name="外校",
            password_hash="fixture-unused",
            role="admin",
            must_change_password=False,
        )
        cls.claims = {}
        for user in [cls.admin, cls.approver, cls.user, cls.other]:
            family = str(uuid4())
            AuthSession.objects.create(
                tenant_id=user.tenant_id,
                user_id=user.pk,
                family_id=family,
                refresh_digest=str(uuid4()),
                expires_at=timezone.now() + timedelta(days=1),
            )
            cls.claims[user.pk] = {
                "tid": user.tenant_id,
                "sub": str(user.pk),
                "fid": family,
            }
        cons = Category.objects.create(
            tenant_id=cls.tenant.pk, code="C", name="耗材", type="consumable"
        )
        fixed = Category.objects.create(
            tenant_id=cls.tenant.pk, code="F", name="资产", type="fixed"
        )
        cls.item = Item.objects.create(
            tenant_id=cls.tenant.pk,
            code="PAPER",
            name="复印纸",
            category_id=cons.pk,
            type="consumable",
            unit="包",
        )
        cls.fixed = Item.objects.create(
            tenant_id=cls.tenant.pk,
            code="PC",
            name="电脑",
            category_id=fixed.pk,
            type="fixed",
            unit="台",
        )
        cls.location = Location.objects.create(tenant_id=cls.tenant.pk, name="总库")
        cls.other_location = Location.objects.create(
            tenant_id=cls.other_tenant.pk, name="外校库"
        )
        cls.supplier = Supplier.objects.create(
            tenant_id=cls.tenant.pk, name="采购供应商"
        )

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE inventory_runtime")

    def request(self, method, path, data=None, user=None, key=None):
        token = access_token(authorize_claims(self.claims[(user or self.admin).pk]))
        return getattr(self.client, method)(
            path,
            data=(data or {}) if method == "get" else json.dumps(data or {}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + token,
            HTTP_IDEMPOTENCY_KEY=key or str(uuid4()),
        )

    def create(self, kind, typ, rows, user=None, **header):
        response = self.request(
            "post", f"/api/v1/{kind}/", {"type": typ, "lines": rows, **header}, user
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["document"]

    def act(self, kind, document, action, data=None, user=None, key=None):
        return self.request(
            "post",
            f"/api/v1/{kind}/{document['id']}/{action}/",
            data,
            user or self.approver,
            key,
        )

    def receive(self, quantity=50, fixed=False, purchase=False):
        row = {
            "item_id": self.fixed.pk if fixed else self.item.pk,
            "quantity": str(quantity),
            "location_id": self.location.pk,
            "quality": "available",
            "unit_price": "200.00",
        }
        return self.create(
            "inbound",
            "purchase" if purchase else "opening",
            [row],
            supplier_id=self.supplier.pk if purchase else None,
        )

    def requisition(self, quantity=10, fixed=False):
        return self.create(
            "outbound",
            "requisition",
            [
                {
                    "item_id": self.fixed.pk if fixed else self.item.pk,
                    "quantity": str(quantity),
                    "location_id": self.location.pk,
                }
            ],
            self.user,
        )

    def execute(self, document, quantity, assets=None, reason=""):
        line = OutboundItem.objects.get(
            tenant_id=self.tenant.pk, order_id=document["id"]
        )
        return self.act(
            "outbound",
            document,
            "execute",
            {
                "lines": [
                    {
                        "id": line.pk,
                        "actual_qty": str(quantity),
                        "asset_ids": assets or [],
                        "shortfall_reason": reason,
                    }
                ]
            },
        )

    def stock(self, quality="available"):
        row = Inventory.objects.filter(
            tenant_id=self.tenant.pk,
            item_id=self.item.pk,
            location_id=self.location.pk,
            quality=quality,
        ).first()
        return row.quantity if row else Decimal(0)

    def snapshot(self, quality="available"):
        response = self.request(
            "get",
            "/api/v1/inventory/observe/",
            {
                "item_id": self.item.pk,
                "location_id": self.location.pk,
                "quality": quality,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        data.pop("item_type")
        return data
