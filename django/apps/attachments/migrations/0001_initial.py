# Explicit ORM state serialization (no DDL), Django 5.2.18 on 2026-10-31 03:33

import django.db.models.deletion
import django.db.models.expressions
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Attachment",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("tenant_id", models.BigIntegerField()),
                        ("object_key", models.TextField()),
                        ("sha256", models.TextField()),
                        ("size_bytes", models.BigIntegerField()),
                        ("content_type", models.TextField()),
                        ("retention_until", models.DateTimeField()),
                        (
                            "legal_hold",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "false", ()
                                )
                            ),
                        ),
                        ("uploaded_by", models.BigIntegerField()),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                        ("deleted_at", models.DateTimeField(null=True)),
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
                            "uploader",
                            models.ForeignObject(
                                from_fields=("tenant_id", "uploaded_by"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "attachments",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "object_key"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="ScrapAttachment",
                    fields=[
                        (
                            "pk",
                            models.CompositePrimaryKey(
                                "tenant_id",
                                "scrap_id",
                                "attachment_id",
                                blank=True,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("tenant_id", models.BigIntegerField()),
                        ("scrap_id", models.BigIntegerField()),
                        ("attachment_id", models.BigIntegerField()),
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
                            "attachment",
                            models.ForeignObject(
                                from_fields=("tenant_id", "attachment_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="attachments.attachment",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "scrap_attachments",
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
            ],
        ),
    ]
