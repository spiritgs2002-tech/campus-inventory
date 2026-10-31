# Explicit ORM state serialization (no DDL), Django 5.2.18 on 2026-10-31 03:33

import django.db.models.deletion
import django.db.models.expressions
import shared.db.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Tenant",
                    fields=[
                        ("id", models.BigAutoField(primary_key=True, serialize=False)),
                        ("code", models.TextField()),
                        ("name", models.TextField()),
                        (
                            "status",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'active'::text", ()
                                )
                            ),
                        ),
                        (
                            "login_failure_limit",
                            models.IntegerField(
                                db_default=django.db.models.expressions.RawSQL("5", ())
                            ),
                        ),
                        (
                            "login_lock_minutes",
                            models.IntegerField(
                                db_default=django.db.models.expressions.RawSQL("15", ())
                            ),
                        ),
                        (
                            "asset_prefix",
                            models.TextField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'AS'::text", ()
                                )
                            ),
                        ),
                        (
                            "next_asset_number",
                            models.BigIntegerField(
                                db_default=django.db.models.expressions.RawSQL("1", ())
                            ),
                        ),
                        ("expire_at", models.DateField(null=True)),
                        (
                            "allow_self_approval",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "false", ()
                                )
                            ),
                        ),
                        (
                            "created_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                ),
                                editable=False,
                            ),
                        ),
                    ],
                    options={
                        "db_table": "tenants",
                        "unique_together": {("code",)},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="Department",
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
                        "db_table": "departments",
                        "unique_together": {("tenant_id", "id"), ("tenant_id", "name")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="User",
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
                        ("username", models.TextField()),
                        ("password_hash", models.TextField()),
                        ("name", models.TextField()),
                        ("role", models.TextField()),
                        ("dept_id", models.BigIntegerField(null=True)),
                        (
                            "must_change_password",
                            models.BooleanField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "true", ()
                                )
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
                        "db_table": "users",
                        "unique_together": {
                            ("tenant_id", "id"),
                            ("tenant_id", "username"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="AuthSession",
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
                        ("user_id", models.BigIntegerField()),
                        ("refresh_digest", models.TextField()),
                        ("expires_at", models.DateTimeField()),
                        ("family_id", models.UUIDField()),
                        ("parent_session_id", models.BigIntegerField(null=True)),
                        ("rotated_at", models.DateTimeField(null=True)),
                        ("revoked_at", models.DateTimeField(null=True)),
                        ("revoked_reason", models.TextField(null=True)),
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
                            "parent_session",
                            models.ForeignObject(
                                from_fields=("tenant_id", "parent_session_id"),
                                null=True,
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.authsession",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                        (
                            "user",
                            models.ForeignObject(
                                from_fields=("tenant_id", "user_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "auth_sessions",
                        "unique_together": {("refresh_digest",), ("tenant_id", "id")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="LoginThrottle",
                    fields=[
                        (
                            "pk",
                            models.CompositePrimaryKey(
                                "tenant_id",
                                "user_id",
                                blank=True,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("tenant_id", models.BigIntegerField()),
                        ("user_id", models.BigIntegerField()),
                        (
                            "failed_login_count",
                            models.IntegerField(
                                db_default=django.db.models.expressions.RawSQL("0", ())
                            ),
                        ),
                        ("locked_until", models.DateTimeField(null=True)),
                        (
                            "user",
                            models.ForeignObject(
                                from_fields=("tenant_id", "user_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "login_throttle",
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="OperationRequest",
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
                        ("actor_id", models.BigIntegerField()),
                        ("operation", models.TextField()),
                        ("idempotency_key", models.TextField()),
                        (
                            "created_xid",
                            shared.db.fields.TransactionIDField(editable=False),
                        ),
                        ("request_digest", models.TextField()),
                        ("response", models.JSONField(null=True)),
                        (
                            "business_refs",
                            models.JSONField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "'[]'::jsonb", ()
                                )
                            ),
                        ),
                        (
                            "completed_at",
                            models.DateTimeField(
                                db_default=django.db.models.expressions.RawSQL(
                                    "now()", ()
                                )
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
                            "actor",
                            models.ForeignObject(
                                from_fields=("tenant_id", "actor_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "operation_requests",
                        "unique_together": {
                            ("tenant_id", "actor_id", "operation", "idempotency_key"),
                            ("tenant_id", "id"),
                        },
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="FileJob",
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
                        ("operator_id", models.BigIntegerField()),
                        ("file_key", models.TextField()),
                        ("file_digest", models.TextField()),
                        ("job_type", models.TextField()),
                        ("template_type", models.TextField()),
                        ("template_version", models.TextField()),
                        ("expires_at", models.DateTimeField()),
                        ("mapping_version", models.TextField()),
                        ("status", models.TextField()),
                        ("result", models.JSONField(null=True)),
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
                            "operator",
                            models.ForeignObject(
                                from_fields=("tenant_id", "operator_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "file_jobs",
                        "unique_together": {("tenant_id", "id")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
                migrations.CreateModel(
                    name="AuditLog",
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
                        ("user_id", models.BigIntegerField()),
                        ("action", models.TextField()),
                        ("object_type", models.TextField()),
                        ("object_id", models.BigIntegerField(null=True)),
                        ("changes", models.JSONField()),
                        ("reason", models.TextField(null=True)),
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
                            "user",
                            models.ForeignObject(
                                from_fields=("tenant_id", "user_id"),
                                on_delete=django.db.models.deletion.DO_NOTHING,
                                related_name="+",
                                to="core.user",
                                to_fields=("tenant_id", "id"),
                            ),
                        ),
                    ],
                    options={
                        "db_table": "audit_logs",
                        "unique_together": {("tenant_id", "id")},
                        "managed": False,
                        "indexes": [],
                        "constraints": [],
                    },
                ),
            ],
        ),
    ]
