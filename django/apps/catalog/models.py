"""Read/write mappings only. schema-v7.sql owns every database constraint."""

from django.db import models
from django.db.models.expressions import RawSQL


class Location(models.Model):
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
        db_table = "locations"
        unique_together = (("tenant_id", "id"), ("tenant_id", "name"))


class Category(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    code = models.TextField()
    name = models.TextField()
    type = models.TextField()
    parent_id = models.BigIntegerField(null=True)
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
    parent = models.ForeignObject(
        "catalog.Category",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "parent_id", "type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "categories"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "code"),
            ("tenant_id", "id", "type"),
        )


class Item(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    code = models.TextField()
    name = models.TextField()
    category_id = models.BigIntegerField()
    type = models.TextField()
    unit = models.TextField()
    spec = models.TextField(null=True)
    reference_price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    safety_stock = models.DecimalField(max_digits=14, decimal_places=3, null=True)
    qr_token = models.TextField(null=True, db_index=True)
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
    category = models.ForeignObject(
        "catalog.Category",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "category_id", "type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "items"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "code"),
            ("tenant_id", "id", "type"),
        )


class Supplier(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    name = models.TextField()
    contact = models.TextField(null=True)
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
        db_table = "suppliers"
        unique_together = (("tenant_id", "id"), ("tenant_id", "name"))
