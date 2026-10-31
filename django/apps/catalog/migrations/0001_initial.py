# Explicit ORM state serialization (no DDL), Django 5.2.18 on 2026-10-31 03:33

import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0002_model_state"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Location",
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
                        ("name", models.TextField()),
                        (
                            "active",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "true", ()
                                )
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
                    ],
                    options={
                        "db_table": "locations",
                        "unique_together": {("tenant_id", "id"), ("tenant_id", "name")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="Category",
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
                        ("code", models.TextField()),
                        ("name", models.TextField()),
                        ("type", models.TextField()),
                        ("parent_id", models.BigIntegerField(null=True)),
                        (
                            "active",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "true", ()
                                )
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
                            "parent",
                            models.ForeignObject(
                                from_fields=("tenant_id", "parent_id", "type"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.category",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "categories",
                        "unique_together": {
                            ("tenant_id", "code"),
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "type"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="Item",
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
                        ("code", models.TextField()),
                        ("name", models.TextField()),
                        ("category_id", models.BigIntegerField()),
                        ("type", models.TextField()),
                        ("unit", models.TextField()),
                        ("spec", models.TextField(null=True)),
                        (
                            "reference_price",
                            models.DecimalField(
                                decimal_places=2, max_digits=14, null=True
                            ),
                        ),
                        (
                            "safety_stock",
                            models.DecimalField(
                                decimal_places=3, max_digits=14, null=True
                            ),
                        ),
                        (
                            "active",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "true", ()
                                )
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
                            "category",
                            models.ForeignObject(
                                from_fields=("tenant_id", "category_id", "type"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="catalog.category",
                                to_fields=("tenant_id", "id", "type"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "items",
                        "unique_together": {
                            ("tenant_id", "code"),
                            ("tenant_id", "id"),
                            ("tenant_id", "id", "type"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="Supplier",
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
                        ("name", models.TextField()),
                        ("contact", models.TextField(null=True)),
                        (
                            "active",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "true", ()
                                )
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
                    ],
                    options={
                        "db_table": "suppliers",
                        "unique_together": {("tenant_id", "id"), ("tenant_id", "name")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
            ],
        ),
    ]
