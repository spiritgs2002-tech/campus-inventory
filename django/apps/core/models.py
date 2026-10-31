"""Read/write mappings only. schema-v7.sql owns every database constraint."""

from django.db import models
from django.db.models.expressions import RawSQL
from shared.db.fields import TransactionIDField


class Tenant(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.TextField()
    name = models.TextField()
    status = models.TextField(db_default=RawSQL("'active'::text", ()))
    login_failure_limit = models.IntegerField(db_default=RawSQL("5", ()))
    login_lock_minutes = models.IntegerField(db_default=RawSQL("15", ()))
    asset_prefix = models.TextField(db_default=RawSQL("'AS'::text", ()))
    next_asset_number = models.BigIntegerField(db_default=RawSQL("1", ()))
    expire_at = models.DateField(null=True)
    allow_self_approval = models.BooleanField(db_default=RawSQL("false", ()))
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)

    class Meta:
        managed = False
        db_table = "tenants"
        unique_together = (("code",),)


class Department(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    name = models.TextField()
    active = models.BooleanField(db_default=RawSQL("true", ()))
    updated_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "departments"
        unique_together = (("tenant_id", "id"), ("tenant_id", "name"))


class User(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    username = models.TextField()
    password_hash = models.TextField()
    name = models.TextField()
    role = models.TextField()
    dept_id = models.BigIntegerField(null=True)
    must_change_password = models.BooleanField(db_default=RawSQL("true", ()))
    active = models.BooleanField(db_default=RawSQL("true", ()))
    updated_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    dept = models.ForeignObject(
        "core.Department",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "dept_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "users"
        unique_together = (("tenant_id", "id"), ("tenant_id", "username"))


class AuthSession(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    user_id = models.BigIntegerField()
    refresh_digest = models.TextField()
    expires_at = models.DateTimeField()
    family_id = models.UUIDField()
    parent_session_id = models.BigIntegerField(null=True)
    rotated_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    revoked_reason = models.TextField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    parent_session = models.ForeignObject(
        "core.AuthSession",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "parent_session_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    user = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "user_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "auth_sessions"
        unique_together = (("tenant_id", "id"), ("refresh_digest",))


class LoginThrottle(models.Model):
    pk = models.CompositePrimaryKey("tenant_id", "user_id")
    tenant_id = models.BigIntegerField()
    user_id = models.BigIntegerField()
    failed_login_count = models.IntegerField(db_default=RawSQL("0", ()))
    locked_until = models.DateTimeField(null=True)

    user = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "user_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "login_throttle"


class OperationRequest(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    actor_id = models.BigIntegerField()
    operation = models.TextField()
    idempotency_key = models.TextField()
    created_xid = TransactionIDField()
    request_digest = models.TextField()
    response = models.JSONField(null=True)
    business_refs = models.JSONField(db_default=RawSQL("'[]'::jsonb", ()))
    completed_at = models.DateTimeField(db_default=RawSQL("now()", ()))

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    actor = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "actor_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "operation_requests"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "actor_id", "operation", "idempotency_key"),
        )


class FileJob(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    operator_id = models.BigIntegerField()
    file_key = models.TextField()
    file_digest = models.TextField()
    job_type = models.TextField()
    template_type = models.TextField()
    template_version = models.TextField()
    expires_at = models.DateTimeField()
    mapping_version = models.TextField()
    status = models.TextField()
    result = models.JSONField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    operator = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operator_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "file_jobs"
        unique_together = (("tenant_id", "id"),)


class AuditLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    user_id = models.BigIntegerField()
    action = models.TextField()
    object_type = models.TextField()
    object_id = models.BigIntegerField(null=True)
    changes = models.JSONField()
    reason = models.TextField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    user = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "user_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "audit_logs"
        unique_together = (("tenant_id", "id"),)
