"""Read/write mappings only. schema-v7.sql owns every database constraint."""

from django.db import models
from django.db.models.expressions import RawSQL


class Attachment(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    object_key = models.TextField()
    sha256 = models.TextField()
    size_bytes = models.BigIntegerField()
    content_type = models.TextField()
    retention_until = models.DateTimeField()
    legal_hold = models.BooleanField(db_default=RawSQL("false", ()))
    uploaded_by = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    deleted_at = models.DateTimeField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    uploader = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "uploaded_by"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "attachments"
        unique_together = (("tenant_id", "id"), ("tenant_id", "object_key"))


class ScrapAttachment(models.Model):
    pk = models.CompositePrimaryKey("tenant_id", "scrap_id", "attachment_id")
    tenant_id = models.BigIntegerField()
    scrap_id = models.BigIntegerField()
    attachment_id = models.BigIntegerField()

    scrap = models.ForeignObject(
        "inventory.ScrapOrder",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "scrap_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    attachment = models.ForeignObject(
        "attachments.Attachment",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "attachment_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "scrap_attachments"
