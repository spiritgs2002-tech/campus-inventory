# Explicit ORM state serialization (no DDL), Django 5.2.18 on 2026-10-31 03:33

import django.db.models.deletion
import django.db.models.expressions
import shared.db.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="InboundOrder",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        (
                            "created_xid",
                            shared.db.fields.TransactionIDField(editable=False),
                        ),
                        ("order_no", models.TextField()),
                        ("type", models.TextField()),
                        ("supplier_id", models.BigIntegerField(null=True)),
                        ("operator_id", models.BigIntegerField()),
                        ("operation_id", models.BigIntegerField()),
                        ("file_job_id", models.BigIntegerField(null=True)),
                        ("transfer_id", models.UUIDField(null=True)),
                        (
                            "executed_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                )
                            ),
                        ),
                        ("remark", models.TextField(null=True)),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "supplier",
                            models.ForeignObject(
                                from_fields=("tenant_id", "supplier_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.supplier",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operator_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operation",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operation_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.operationrequest",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "file_job",
                            models.ForeignObject(
                                from_fields=("tenant_id", "file_job_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.filejob",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "inbound_orders",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "order_no"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="InboundItem",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("order_id", models.BigIntegerField()),
                        ("item_id", models.BigIntegerField()),
                        ("item_type", models.TextField()),
                        (
                            "quantity",
                            models.DecimalField(decimal_places=3, max_digits=14),
                        ),
                        (
                            "unit_price",
                            models.DecimalField(
                                decimal_places=2, max_digits=14, null=True
                            ),
                        ),
                        ("location_id", models.BigIntegerField()),
                        ("quality", models.TextField()),
                        ("source_outbound_item_id", models.BigIntegerField(null=True)),
                        ("source_outbound_asset_id", models.BigIntegerField(null=True)),
                        ("adjustment_item_id", models.BigIntegerField(null=True)),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "order",
                            models.ForeignObject(
                                from_fields=("tenant_id", "order_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.inboundorder",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "item_id", "item_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.item",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "adjustment_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "adjustment_item_id",
                                    "item_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.adjustmentitem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "source_outbound_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_outbound_item_id",
                                    "item_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.outbounditem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "source_outbound_asset",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_outbound_asset_id",
                                    "item_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.outboundasset",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "inbound_items",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "item_id"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="AssetCard",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("asset_no", models.TextField()),
                        ("item_id", models.BigIntegerField()),
                        (
                            "item_type",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'fixed'::text", ()
                                )
                            ),
                        ),
                        ("source_inbound_item_id", models.BigIntegerField()),
                        ("location_id", models.BigIntegerField()),
                        ("responsible_user_id", models.BigIntegerField(null=True)),
                        ("status", models.TextField()),
                        ("qr_token", models.TextField()),
                        (
                            "original_value",
                            models.DecimalField(
                                decimal_places=2, max_digits=14, null=True
                            ),
                        ),
                        ("purchase_date", models.DateField(null=True)),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "item_id", "item_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.item",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                        (
                            "source_inbound_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_inbound_item_id",
                                    "item_id",
                                ),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.inbounditem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "responsible_user",
                            models.ForeignObject(
                                from_fields=("tenant_id", "responsible_user_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "asset_cards",
                        "unique_together": {
                            ("qr_token",),
                            ("tenant_id", "asset_no"),
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "item_id"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="OutboundOrder",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("order_no", models.TextField()),
                        ("type", models.TextField()),
                        (
                            "status",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'pending'::text", ()
                                )
                            ),
                        ),
                        ("applicant_id", models.BigIntegerField()),
                        ("operator_id", models.BigIntegerField(null=True)),
                        ("operation_id", models.BigIntegerField(null=True)),
                        ("executed_at", models.DateTimeField(null=True)),
                        ("remark", models.TextField(null=True)),
                        ("transfer_id", models.UUIDField(null=True)),
                        (
                            "self_issued",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "false", ()
                                )
                            ),
                        ),
                        ("dept_id", models.BigIntegerField(null=True)),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "applicant",
                            models.ForeignObject(
                                from_fields=("tenant_id", "applicant_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operator_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operation",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operation_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.operationrequest",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "dept",
                            models.ForeignObject(
                                from_fields=("tenant_id", "dept_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.department",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "outbound_orders",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "order_no"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="OutboundItem",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("order_id", models.BigIntegerField()),
                        ("item_id", models.BigIntegerField()),
                        ("item_type", models.TextField()),
                        (
                            "requested_qty",
                            models.DecimalField(decimal_places=3, max_digits=14),
                        ),
                        (
                            "actual_qty",
                            models.DecimalField(
                                db_default=django.db.models.expressions.RawSQL("0", ()),
                                decimal_places=3,
                                max_digits=14,
                            ),
                        ),
                        ("source_inbound_item_id", models.BigIntegerField(null=True)),
                        ("location_id", models.BigIntegerField(null=True)),
                        ("recipient_id", models.BigIntegerField(null=True)),
                        ("shortfall_reason", models.TextField(null=True)),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "order",
                            models.ForeignObject(
                                from_fields=("tenant_id", "order_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.outboundorder",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "item_id", "item_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.item",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                        (
                            "source_inbound_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_inbound_item_id",
                                    "item_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.inbounditem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "recipient",
                            models.ForeignObject(
                                from_fields=("tenant_id", "recipient_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "outbound_items",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "item_id"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="OutboundAsset",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("outbound_item_id", models.BigIntegerField()),
                        ("item_id", models.BigIntegerField()),
                        ("asset_card_id", models.BigIntegerField()),
                        ("closed_at", models.DateTimeField(null=True)),
                        ("close_reason", models.TextField(null=True)),
                        ("loss_scrap_item_id", models.BigIntegerField(null=True)),
                        (
                            "loss_type",
                            models.GeneratedField(
                                db_persist=True,
                                expression=models.Value("loss"),
                                null=True,
                                output_field=models.TextField(),
                            ),
                        ),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "outbound_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "outbound_item_id",
                                    "item_id",
                                ),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.outbounditem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "asset_card",
                            models.ForeignObject(
                                from_fields=("tenant_id", "asset_card_id", "item_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.assetcard",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "loss_scrap_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "loss_scrap_item_id",
                                    "asset_card_id",
                                    "loss_type",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scrapitem",
                                to_fields=(
                                    "tenant_id",
                                    "id",
                                    "asset_card_id",
                                    "disposal_type",
                                ),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "outbound_assets",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "asset_card_id"),
                            ("tenant_id", "id", "item_id"),
                            ("tenant_id", "outbound_item_id", "asset_card_id"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="Inventory",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("item_id", models.BigIntegerField()),
                        (
                            "item_type",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'consumable'::text", ()
                                )
                            ),
                        ),
                        ("location_id", models.BigIntegerField()),
                        ("quality", models.TextField()),
                        (
                            "quantity",
                            models.DecimalField(
                                db_default=django.db.models.expressions.RawSQL("0", ()),
                                decimal_places=3,
                                max_digits=14,
                            ),
                        ),
                        (
                            "version",
                            models.BigIntegerField(
                                db_default=django.db.models.expressions.RawSQL("0", ())
                            ),
                        ),
                        (
                            "updated_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "item_id", "item_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.item",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "inventory",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "item_id", "location_id", "quality"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="ScrapOrder",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("order_no", models.TextField()),
                        ("disposal_type", models.TextField()),
                        (
                            "status",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'pending'::text", ()
                                )
                            ),
                        ),
                        ("applicant_id", models.BigIntegerField()),
                        ("approver_id", models.BigIntegerField(null=True)),
                        ("approved_at", models.DateTimeField(null=True)),
                        (
                            "self_approved",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "false", ()
                                )
                            ),
                        ),
                        (
                            "allow_self_approval_snapshot",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "false", ()
                                )
                            ),
                        ),
                        ("responsibility_opinion", models.TextField(null=True)),
                        ("operation_id", models.BigIntegerField(null=True)),
                        ("reason", models.TextField()),
                        ("decided_by", models.BigIntegerField(null=True)),
                        ("decided_at", models.DateTimeField(null=True)),
                        ("decision_reason", models.TextField(null=True)),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "applicant",
                            models.ForeignObject(
                                from_fields=("tenant_id", "applicant_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "approver",
                            models.ForeignObject(
                                from_fields=("tenant_id", "approver_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operation",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operation_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.operationrequest",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "decider",
                            models.ForeignObject(
                                from_fields=("tenant_id", "decided_by"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "scrap_orders",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "disposal_type"),
                            ("tenant_id", "order_no"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="ScrapItem",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("scrap_id", models.BigIntegerField()),
                        ("item_id", models.BigIntegerField()),
                        ("item_type", models.TextField()),
                        (
                            "quantity",
                            models.DecimalField(
                                decimal_places=3, max_digits=14, null=True
                            ),
                        ),
                        ("asset_card_id", models.BigIntegerField(null=True)),
                        ("location_id", models.BigIntegerField(null=True)),
                        ("quality", models.TextField(null=True)),
                        ("source_loss_item_id", models.BigIntegerField(null=True)),
                        ("recovery_location_id", models.BigIntegerField(null=True)),
                        ("recovery_quality", models.TextField(null=True)),
                        ("source_outbound_asset_id", models.BigIntegerField(null=True)),
                        (
                            "residual_value",
                            models.DecimalField(
                                decimal_places=2, max_digits=14, null=True
                            ),
                        ),
                        ("disposal_type", models.TextField()),
                        (
                            "source_loss_type",
                            models.GeneratedField(
                                db_persist=True,
                                expression=models.Value("loss"),
                                null=True,
                                output_field=models.TextField(),
                            ),
                        ),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "source_loss_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_loss_item_id",
                                    "asset_card_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scrapitem",
                                to_fields=("tenant_id", "id", "asset_card_id"),
                            ),
                        ),
                        (
                            "recovery_location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "recovery_location_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "scrap",
                            models.ForeignObject(
                                from_fields=("tenant_id", "scrap_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scraporder",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "item_id", "item_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.item",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                        (
                            "asset_card",
                            models.ForeignObject(
                                from_fields=("tenant_id", "asset_card_id", "item_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.assetcard",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "source_outbound_asset",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_outbound_asset_id",
                                    "item_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.outboundasset",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "scrap_qualified",
                            models.ForeignObject(
                                from_fields=("tenant_id", "scrap_id", "disposal_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scraporder",
                                to_fields=("tenant_id", "id", "disposal_type"),
                            ),
                        ),
                        (
                            "source_loss_item_qualified",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_loss_item_id",
                                    "asset_card_id",
                                    "source_loss_type",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scrapitem",
                                to_fields=(
                                    "tenant_id",
                                    "id",
                                    "asset_card_id",
                                    "disposal_type",
                                ),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "scrap_items",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "asset_card_id"),
                            ("tenant_id", "id", "asset_card_id", "disposal_type"),
                            ("tenant_id", "id", "item_id"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="AdjustmentOrder",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("order_no", models.TextField()),
                        ("type", models.TextField()),
                        (
                            "status",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'pending'::text", ()
                                )
                            ),
                        ),
                        ("applicant_id", models.BigIntegerField()),
                        ("approver_id", models.BigIntegerField(null=True)),
                        ("approved_at", models.DateTimeField(null=True)),
                        ("operation_id", models.BigIntegerField(null=True)),
                        (
                            "self_approved",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "false", ()
                                )
                            ),
                        ),
                        (
                            "allow_self_approval_snapshot",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "false", ()
                                )
                            ),
                        ),
                        ("reason_category", models.TextField()),
                        ("reason", models.TextField()),
                        ("decided_by", models.BigIntegerField(null=True)),
                        ("decided_at", models.DateTimeField(null=True)),
                        ("decision_reason", models.TextField(null=True)),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "applicant",
                            models.ForeignObject(
                                from_fields=("tenant_id", "applicant_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "approver",
                            models.ForeignObject(
                                from_fields=("tenant_id", "approver_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operation",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operation_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.operationrequest",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "decider",
                            models.ForeignObject(
                                from_fields=("tenant_id", "decided_by"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "adjustment_orders",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "type"),
                            ("tenant_id", "order_no"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="AdjustmentItem",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("order_id", models.BigIntegerField()),
                        ("item_id", models.BigIntegerField()),
                        ("item_type", models.TextField()),
                        ("location_id", models.BigIntegerField()),
                        ("direction", models.TextField()),
                        ("quality", models.TextField()),
                        ("to_quality", models.TextField(null=True)),
                        (
                            "quantity",
                            models.DecimalField(decimal_places=3, max_digits=14),
                        ),
                        ("observed_at", models.DateTimeField()),
                        (
                            "book_qty",
                            models.DecimalField(
                                decimal_places=3, max_digits=14, null=True
                            ),
                        ),
                        (
                            "counted_qty",
                            models.DecimalField(
                                decimal_places=3, max_digits=14, null=True
                            ),
                        ),
                        ("inventory_version", models.BigIntegerField(null=True)),
                        ("target_inventory_version", models.BigIntegerField(null=True)),
                        (
                            "original_value",
                            models.DecimalField(
                                decimal_places=2, max_digits=14, null=True
                            ),
                        ),
                        ("adjustment_type", models.TextField()),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "order",
                            models.ForeignObject(
                                from_fields=("tenant_id", "order_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.adjustmentorder",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "item_id", "item_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.item",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "order_qualified",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "order_id",
                                    "adjustment_type",
                                ),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.adjustmentorder",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "adjustment_items",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "item_id"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="StockLedger",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("item_id", models.BigIntegerField()),
                        (
                            "item_type",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'consumable'::text", ()
                                )
                            ),
                        ),
                        ("location_id", models.BigIntegerField()),
                        ("quality", models.TextField()),
                        ("direction", models.TextField()),
                        (
                            "quantity",
                            models.DecimalField(decimal_places=3, max_digits=14),
                        ),
                        (
                            "balance_after",
                            models.DecimalField(decimal_places=3, max_digits=14),
                        ),
                        ("inbound_item_id", models.BigIntegerField(null=True)),
                        ("outbound_item_id", models.BigIntegerField(null=True)),
                        ("scrap_item_id", models.BigIntegerField(null=True)),
                        ("adjustment_item_id", models.BigIntegerField(null=True)),
                        ("operator_id", models.BigIntegerField()),
                        ("operation_id", models.BigIntegerField()),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "item_id", "item_type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.item",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "inbound_item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "inbound_item_id", "item_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.inbounditem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "outbound_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "outbound_item_id",
                                    "item_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.outbounditem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "scrap_item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "scrap_item_id", "item_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scrapitem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operator_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operation",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operation_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.operationrequest",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "adjustment_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "adjustment_item_id",
                                    "item_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.adjustmentitem",
                                to_fields=("tenant_id", "id", "item_id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "stock_ledger",
                        "unique_together": {("tenant_id", "id")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="AssetEvent",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("asset_card_id", models.BigIntegerField()),
                        ("action", models.TextField()),
                        ("from_status", models.TextField(null=True)),
                        ("to_status", models.TextField()),
                        ("inbound_item_id", models.BigIntegerField(null=True)),
                        ("outbound_asset_id", models.BigIntegerField(null=True)),
                        ("scrap_item_id", models.BigIntegerField(null=True)),
                        ("adjustment_item_id", models.BigIntegerField(null=True)),
                        ("transfer_id", models.UUIDField(null=True)),
                        ("source_loss_item_id", models.BigIntegerField(null=True)),
                        ("location_id", models.BigIntegerField()),
                        ("responsible_user_id", models.BigIntegerField(null=True)),
                        ("operator_id", models.BigIntegerField()),
                        ("operation_id", models.BigIntegerField()),
                        ("reason", models.TextField(null=True)),
                        (
                            "scrap_event_type",
                            models.GeneratedField(
                                db_persist=True,
                                expression=models.Case(
                                    models.When(
                                        action="scrap", then=models.Value("scrap")
                                    ),
                                    models.When(
                                        action="loss", then=models.Value("loss")
                                    ),
                                    models.When(
                                        action="found", then=models.Value("recovery")
                                    ),
                                    default=None,
                                    output_field=models.TextField(),
                                ),
                                null=True,
                                output_field=models.TextField(),
                            ),
                        ),
                        (
                            "source_loss_type",
                            models.GeneratedField(
                                db_persist=True,
                                expression=models.Value("loss"),
                                null=True,
                                output_field=models.TextField(),
                            ),
                        ),
                        (
                            "tenant",
                            models.ForeignObject(
                                from_fields=("tenant_id",),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.tenant",
                                to_fields=("id",),
                            ),
                        ),
                        (
                            "asset_card",
                            models.ForeignObject(
                                from_fields=("tenant_id", "asset_card_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.assetcard",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "inbound_item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "inbound_item_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.inbounditem",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "outbound_asset",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "outbound_asset_id",
                                    "asset_card_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.outboundasset",
                                to_fields=("tenant_id", "id", "asset_card_id"),
                            ),
                        ),
                        (
                            "scrap_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "scrap_item_id",
                                    "asset_card_id",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scrapitem",
                                to_fields=("tenant_id", "id", "asset_card_id"),
                            ),
                        ),
                        (
                            "operator",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operator_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "operation",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operation_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.operationrequest",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "adjustment_item",
                            models.ForeignObject(
                                from_fields=("tenant_id", "adjustment_item_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.adjustmentitem",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "source_loss_item",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "source_loss_item_id",
                                    "asset_card_id",
                                    "source_loss_type",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scrapitem",
                                to_fields=(
                                    "tenant_id",
                                    "id",
                                    "asset_card_id",
                                    "disposal_type",
                                ),
                            ),
                        ),
                        (
                            "scrap_item_qualified",
                            models.ForeignObject(
                                from_fields=(
                                    "tenant_id",
                                    "scrap_item_id",
                                    "asset_card_id",
                                    "scrap_event_type",
                                ),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="inventory.scrapitem",
                                to_fields=(
                                    "tenant_id",
                                    "id",
                                    "asset_card_id",
                                    "disposal_type",
                                ),
                            ),
                        ),
                        (
                            "location",
                            models.ForeignObject(
                                from_fields=("tenant_id", "location_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.location",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "responsible_user",
                            models.ForeignObject(
                                from_fields=("tenant_id", "responsible_user_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "asset_events",
                        "unique_together": {("tenant_id", "id")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
            ],
        ),
    ]
