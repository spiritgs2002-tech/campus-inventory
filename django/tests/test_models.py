from io import StringIO
from uuid import uuid4

from django.apps import apps
from django.core.management import call_command
from django.db import IntegrityError, ProgrammingError, connection, models, transaction
from django.test import SimpleTestCase, TestCase

from apps.attachments.models import Attachment, ScrapAttachment
from apps.catalog.models import Category, Item, Location
from apps.core.models import Department, LoginThrottle, OperationRequest, Tenant, User
from apps.inventory.models import (
    AssetCard,
    AssetEvent,
    InboundItem,
    InboundOrder,
    Inventory,
    OutboundAsset,
    OutboundItem,
    OutboundOrder,
    ScrapItem,
    ScrapOrder,
)


def business_models():
    return [
        model
        for model in apps.get_models()
        if model._meta.app_label in {"core", "catalog", "inventory", "attachments"}
    ]


class ModelContractTests(SimpleTestCase):
    def test_packaged_schema_matches_authoritative_source_when_present(self):
        from django.conf import settings

        source = settings.BASE_DIR.parent / "schema-v7.sql"
        if source.exists():
            self.assertEqual(
                source.read_bytes(),
                (settings.SQL_ROOT / "schema-v7-current.sql").read_bytes(),
            )
        for name in (
            "grant-runtime.sql",
            "verify-runtime.sql",
            "precheck-sql.py",
            "test-negative.sql",
        ):
            source = settings.BASE_DIR.parent / "v7-tools" / name
            if source.exists():
                self.assertEqual(
                    source.read_bytes(), (settings.SQL_ROOT / name).read_bytes()
                )

    def test_all_models_are_unmanaged_and_composite_keys_have_no_fake_id(self):
        self.assertEqual(len(business_models()), 27)
        for model in business_models():
            self.assertFalse(model._meta.managed, model.__name__)
        for model in (LoginThrottle, ScrapAttachment):
            self.assertIsInstance(model._meta.pk, models.CompositePrimaryKey)
            self.assertNotIn("id", {f.name for f in model._meta.local_fields})


class BaselineDatabaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="P1", name="测试校")
        cls.other_tenant = Tenant.objects.create(code="OTHER", name="另一校")
        cls.department = Department.objects.create(tenant_id=cls.tenant.pk, name="总务")
        cls.user = User.objects.create(
            tenant_id=cls.tenant.pk,
            username="operator",
            name="测试用户",
            password_hash="test-fixture-not-a-real-credential",
            role="admin",
            dept_id=cls.department.pk,
        )
        cls.location = Location.objects.create(tenant_id=cls.tenant.pk, name="仓库")
        cls.category = Category.objects.create(
            tenant_id=cls.tenant.pk, code="FIX", name="固定资产", type="fixed"
        )
        cls.item = Item.objects.create(
            tenant_id=cls.tenant.pk,
            code="PC",
            name="电脑",
            type="fixed",
            category_id=cls.category.pk,
            unit="台",
        )
        cls.operation = OperationRequest.objects.create(
            tenant_id=cls.tenant.pk,
            actor_id=cls.user.pk,
            operation="p1-test",
            idempotency_key=str(uuid4()),
            request_digest="fixture",
        )

    def test_g0_and_runtime_commands(self):
        for command in ("verify_schema", "verify_runtime"):
            call_command(command, stdout=StringIO())

    def test_every_column_and_foreign_key_matches_postgresql(self):
        for model in business_models():
            with (
                self.subTest(table=model._meta.db_table),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    SELECT a.attname, format_type(a.atttypid,a.atttypmod),
                           a.attnotnull, a.attgenerated
                    FROM pg_attribute a
                    WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped
                """,
                    [model._meta.db_table],
                )
                actual = {row[0]: row[1:] for row in cursor.fetchall()}
                fields = {
                    f.column: f
                    for f in model._meta.local_concrete_fields
                    if not isinstance(f, models.CompositePrimaryKey)
                }
                self.assertEqual(set(fields), set(actual))
                for column, field in fields.items():
                    sql_type, notnull, generated = actual[column]
                    self.assertEqual(field.null, not notnull, (model, column))
                    if isinstance(field, models.DecimalField):
                        self.assertEqual(
                            sql_type,
                            f"numeric({field.max_digits},{field.decimal_places})",
                        )
                    if generated:
                        self.assertIsInstance(field, models.GeneratedField)
                cursor.execute(
                    """
                    SELECT rt.relname,
                        ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(num,ord)
                              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.ord),
                        ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(num,ord)
                              JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.ord)
                    FROM pg_constraint c JOIN pg_class rt ON rt.oid=c.confrelid
                    WHERE c.conrelid=%s::regclass AND c.contype='f'
                """,
                    [model._meta.db_table],
                )
                expected_fks = {
                    (t, tuple(local), tuple(remote))
                    for t, local, remote in cursor.fetchall()
                }
                orm_fks = {
                    (
                        f.remote_field.model._meta.db_table,
                        tuple(f.from_fields),
                        tuple(f.to_fields),
                    )
                    for f in model._meta.local_fields
                    if isinstance(f, models.ForeignObject)
                }
                self.assertEqual(orm_fks, expected_fks)

    def test_defaults_trigger_xid_and_composite_joins(self):
        self.assertEqual(self.tenant.status, "active")
        self.assertEqual(self.tenant.next_asset_number, 1)
        self.assertEqual(self.operation.business_refs, [])
        self.assertTrue(str(self.operation.created_xid).isdigit())
        user = User.objects.select_related("dept", "tenant").get(pk=self.user.pk)
        self.assertEqual(user.dept.pk, self.department.pk)
        item = Item.objects.select_related("category").get(pk=self.item.pk)
        self.assertEqual(item.category.pk, self.category.pk)
        # Nullable composite relations must use an outer join, not drop the row.
        self.assertIsNone(
            Category.objects.select_related("parent").get(pk=self.category.pk).parent
        )

    def test_cross_tenant_composite_foreign_key_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(
                tenant_id=self.other_tenant.pk,
                username="bad",
                name="bad",
                password_hash="fixture",
                role="user",
                dept_id=self.department.pk,
            )

    def test_composite_primary_key_read_write(self):
        throttle = LoginThrottle.objects.create(
            tenant_id=self.tenant.pk, user_id=self.user.pk
        )
        self.assertEqual(throttle.pk, (self.tenant.pk, self.user.pk))
        throttle.failed_login_count = 2
        throttle.save(update_fields=["failed_login_count"])
        self.assertEqual(
            LoginThrottle.objects.get(pk=throttle.pk).failed_login_count, 2
        )

    def test_generated_columns_and_trigger_stamped_inbound_roundtrip(self):
        inbound = InboundOrder.objects.create(
            tenant_id=self.tenant.pk,
            order_no="IN-P1",
            type="opening",
            operator_id=self.user.pk,
            operation_id=self.operation.pk,
        )
        line = InboundItem.objects.create(
            tenant_id=self.tenant.pk,
            order_id=inbound.pk,
            item_id=self.item.pk,
            item_type="fixed",
            quantity=1,
            location_id=self.location.pk,
            quality="available",
        )
        card = AssetCard.objects.create(
            tenant_id=self.tenant.pk,
            asset_no="AS-P1",
            item_id=self.item.pk,
            source_inbound_item_id=line.pk,
            location_id=self.location.pk,
            status="in_stock",
            qr_token=str(uuid4()),
        )
        outbound = OutboundOrder.objects.create(
            tenant_id=self.tenant.pk,
            order_no="OUT-P1",
            type="requisition",
            applicant_id=self.user.pk,
        )
        out_line = OutboundItem.objects.create(
            tenant_id=self.tenant.pk,
            order_id=outbound.pk,
            item_id=self.item.pk,
            item_type="fixed",
            requested_qty=1,
        )
        issue = OutboundAsset.objects.create(
            tenant_id=self.tenant.pk,
            outbound_item_id=out_line.pk,
            item_id=self.item.pk,
            asset_card_id=card.pk,
        )
        scrap = ScrapOrder.objects.create(
            tenant_id=self.tenant.pk,
            order_no="LOSS-P1",
            disposal_type="loss",
            applicant_id=self.user.pk,
            reason="测试",
        )
        scrap_line = ScrapItem.objects.create(
            tenant_id=self.tenant.pk,
            scrap_id=scrap.pk,
            item_id=self.item.pk,
            item_type="fixed",
            asset_card_id=card.pk,
            disposal_type="loss",
        )
        event = AssetEvent.objects.create(
            tenant_id=self.tenant.pk,
            asset_card_id=card.pk,
            action="loss",
            from_status="in_stock",
            to_status="lost",
            scrap_item_id=scrap_line.pk,
            location_id=self.location.pk,
            operator_id=self.user.pk,
            operation_id=self.operation.pk,
        )
        self.assertEqual(issue.loss_type, "loss")
        self.assertEqual(scrap_line.source_loss_type, "loss")
        self.assertEqual(event.scrap_event_type, "loss")
        self.assertEqual(str(inbound.created_xid), str(self.operation.created_xid))
        attachment = Attachment.objects.create(
            tenant_id=self.tenant.pk,
            object_key="P1/evidence",
            sha256="a" * 64,
            size_bytes=10,
            content_type="text/plain",
            retention_until=inbound.executed_at,
            uploaded_by=self.user.pk,
        )
        link = ScrapAttachment.objects.create(
            tenant_id=self.tenant.pk, scrap_id=scrap.pk, attachment_id=attachment.pk
        )
        self.assertEqual(
            ScrapAttachment.objects.get(pk=link.pk).attachment.pk, attachment.pk
        )

    def test_inventory_version_is_database_managed(self):
        category = Category.objects.create(
            tenant_id=self.tenant.pk, code="CON", name="耗材", type="consumable"
        )
        item = Item.objects.create(
            tenant_id=self.tenant.pk,
            category_id=category.pk,
            code="PAPER",
            name="纸",
            type="consumable",
            unit="包",
        )
        stock = Inventory.objects.create(
            tenant_id=self.tenant.pk,
            item_id=item.pk,
            location_id=self.location.pk,
            quality="available",
        )
        stock.quantity = 3
        stock.save(update_fields=["quantity"])
        stock.refresh_from_db()
        self.assertEqual(stock.version, 1)

    def test_runtime_effective_permissions_and_migration_journal_protection(self):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE inventory_runtime")
        try:
            location = Location.objects.create(
                tenant_id=self.tenant.pk, name="运行身份建库位"
            )
            self.assertIsNotNone(location.pk)
            for statement in (
                "CREATE TABLE public.forbidden_p1 (id bigint)",
                "TRUNCATE TABLE locations",
                "DELETE FROM locations",
                "UPDATE tenants SET status='disabled'",
                "UPDATE django_migrations SET name=name",
                "INSERT INTO django_migrations(app,name,applied) VALUES ('bad','bad',now())",
            ):
                with (
                    self.subTest(sql=statement),
                    self.assertRaises(ProgrammingError),
                    transaction.atomic(),
                ):
                    with connection.cursor() as cursor:
                        cursor.execute(statement)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")
