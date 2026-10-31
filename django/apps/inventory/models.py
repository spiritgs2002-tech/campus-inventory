"""Read/write mappings only. schema-v7.sql owns every database constraint."""

from django.db import models
from django.db.models.expressions import RawSQL
from shared.db.fields import TransactionIDField


class InboundOrder(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    created_xid = TransactionIDField()
    order_no = models.TextField()
    type = models.TextField()
    supplier_id = models.BigIntegerField(null=True)
    operator_id = models.BigIntegerField()
    operation_id = models.BigIntegerField()
    file_job_id = models.BigIntegerField(null=True)
    transfer_id = models.UUIDField(null=True)
    executed_at = models.DateTimeField(db_default=RawSQL("now()", ()))
    remark = models.TextField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    supplier = models.ForeignObject(
        "catalog.Supplier",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "supplier_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    operator = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operator_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    operation = models.ForeignObject(
        "core.OperationRequest",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operation_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    file_job = models.ForeignObject(
        "core.FileJob",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "file_job_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "inbound_orders"
        unique_together = (("tenant_id", "id"), ("tenant_id", "order_no"))


class InboundItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    order_id = models.BigIntegerField()
    item_id = models.BigIntegerField()
    item_type = models.TextField()
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    location_id = models.BigIntegerField()
    quality = models.TextField()
    source_outbound_item_id = models.BigIntegerField(null=True)
    source_outbound_asset_id = models.BigIntegerField(null=True)
    adjustment_item_id = models.BigIntegerField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    order = models.ForeignObject(
        "inventory.InboundOrder",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "order_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    item = models.ForeignObject(
        "catalog.Item",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "item_id", "item_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    adjustment_item = models.ForeignObject(
        "inventory.AdjustmentItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "adjustment_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    source_outbound_item = models.ForeignObject(
        "inventory.OutboundItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "source_outbound_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    source_outbound_asset = models.ForeignObject(
        "inventory.OutboundAsset",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "source_outbound_asset_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "inbound_items"
        unique_together = (("tenant_id", "id"), ("tenant_id", "id", "item_id"))


class AssetCard(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    asset_no = models.TextField()
    item_id = models.BigIntegerField()
    item_type = models.TextField(db_default=RawSQL("'fixed'::text", ()))
    source_inbound_item_id = models.BigIntegerField()
    location_id = models.BigIntegerField()
    responsible_user_id = models.BigIntegerField(null=True)
    status = models.TextField()
    qr_token = models.TextField()
    original_value = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    purchase_date = models.DateField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    item = models.ForeignObject(
        "catalog.Item",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "item_id", "item_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )
    source_inbound_item = models.ForeignObject(
        "inventory.InboundItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "source_inbound_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=False,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    responsible_user = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "responsible_user_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "asset_cards"
        unique_together = (
            ("tenant_id", "id"),
            ("qr_token",),
            ("tenant_id", "asset_no"),
            ("tenant_id", "id", "item_id"),
        )


class OutboundOrder(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    order_no = models.TextField()
    type = models.TextField()
    status = models.TextField(db_default=RawSQL("'pending'::text", ()))
    applicant_id = models.BigIntegerField()
    operator_id = models.BigIntegerField(null=True)
    operation_id = models.BigIntegerField(null=True)
    executed_at = models.DateTimeField(null=True)
    remark = models.TextField(null=True)
    transfer_id = models.UUIDField(null=True)
    self_issued = models.BooleanField(db_default=RawSQL("false", ()))
    dept_id = models.BigIntegerField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    applicant = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "applicant_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    operator = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operator_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    operation = models.ForeignObject(
        "core.OperationRequest",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operation_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
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
        db_table = "outbound_orders"
        unique_together = (("tenant_id", "id"), ("tenant_id", "order_no"))


class OutboundItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    order_id = models.BigIntegerField()
    item_id = models.BigIntegerField()
    item_type = models.TextField()
    requested_qty = models.DecimalField(max_digits=14, decimal_places=3)
    actual_qty = models.DecimalField(
        max_digits=14, decimal_places=3, db_default=RawSQL("0", ())
    )
    source_inbound_item_id = models.BigIntegerField(null=True)
    location_id = models.BigIntegerField(null=True)
    recipient_id = models.BigIntegerField(null=True)
    shortfall_reason = models.TextField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    order = models.ForeignObject(
        "inventory.OutboundOrder",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "order_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    item = models.ForeignObject(
        "catalog.Item",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "item_id", "item_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )
    source_inbound_item = models.ForeignObject(
        "inventory.InboundItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "source_inbound_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    recipient = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "recipient_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "outbound_items"
        unique_together = (("tenant_id", "id"), ("tenant_id", "id", "item_id"))


class OutboundAsset(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    outbound_item_id = models.BigIntegerField()
    item_id = models.BigIntegerField()
    asset_card_id = models.BigIntegerField()
    closed_at = models.DateTimeField(null=True)
    close_reason = models.TextField(null=True)
    loss_scrap_item_id = models.BigIntegerField(null=True)
    loss_type = models.GeneratedField(
        expression=models.Value("loss"),
        output_field=models.TextField(),
        db_persist=True,
        null=True,
    )

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    outbound_item = models.ForeignObject(
        "inventory.OutboundItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "outbound_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=False,
    )
    asset_card = models.ForeignObject(
        "inventory.AssetCard",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "asset_card_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=False,
    )
    loss_scrap_item = models.ForeignObject(
        "inventory.ScrapItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "loss_scrap_item_id", "asset_card_id", "loss_type"),
        to_fields=("tenant_id", "id", "asset_card_id", "disposal_type"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "outbound_assets"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "outbound_item_id", "asset_card_id"),
            ("tenant_id", "id", "item_id"),
            ("tenant_id", "id", "asset_card_id"),
        )


class Inventory(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    item_id = models.BigIntegerField()
    item_type = models.TextField(db_default=RawSQL("'consumable'::text", ()))
    location_id = models.BigIntegerField()
    quality = models.TextField()
    quantity = models.DecimalField(
        max_digits=14, decimal_places=3, db_default=RawSQL("0", ())
    )
    version = models.BigIntegerField(db_default=RawSQL("0", ()))
    updated_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    item = models.ForeignObject(
        "catalog.Item",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "item_id", "item_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "inventory"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "item_id", "location_id", "quality"),
        )


class ScrapOrder(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    order_no = models.TextField()
    disposal_type = models.TextField()
    status = models.TextField(db_default=RawSQL("'pending'::text", ()))
    applicant_id = models.BigIntegerField()
    approver_id = models.BigIntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    self_approved = models.BooleanField(db_default=RawSQL("false", ()))
    allow_self_approval_snapshot = models.BooleanField(db_default=RawSQL("false", ()))
    responsibility_opinion = models.TextField(null=True)
    operation_id = models.BigIntegerField(null=True)
    reason = models.TextField()
    decided_by = models.BigIntegerField(null=True)
    decided_at = models.DateTimeField(null=True)
    decision_reason = models.TextField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    applicant = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "applicant_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    approver = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "approver_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    operation = models.ForeignObject(
        "core.OperationRequest",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operation_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    decider = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "decided_by"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "scrap_orders"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "order_no"),
            ("tenant_id", "id", "disposal_type"),
        )


class ScrapItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    scrap_id = models.BigIntegerField()
    item_id = models.BigIntegerField()
    item_type = models.TextField()
    quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True)
    asset_card_id = models.BigIntegerField(null=True)
    location_id = models.BigIntegerField(null=True)
    quality = models.TextField(null=True)
    source_loss_item_id = models.BigIntegerField(null=True)
    recovery_location_id = models.BigIntegerField(null=True)
    recovery_quality = models.TextField(null=True)
    source_outbound_asset_id = models.BigIntegerField(null=True)
    residual_value = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    disposal_type = models.TextField()
    source_loss_type = models.GeneratedField(
        expression=models.Value("loss"),
        output_field=models.TextField(),
        db_persist=True,
        null=True,
    )

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    source_loss_item = models.ForeignObject(
        "inventory.ScrapItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "source_loss_item_id", "asset_card_id"),
        to_fields=("tenant_id", "id", "asset_card_id"),
        related_name="+",
        null=True,
    )
    recovery_location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "recovery_location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    scrap = models.ForeignObject(
        "inventory.ScrapOrder",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "scrap_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    item = models.ForeignObject(
        "catalog.Item",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "item_id", "item_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )
    asset_card = models.ForeignObject(
        "inventory.AssetCard",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "asset_card_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    source_outbound_asset = models.ForeignObject(
        "inventory.OutboundAsset",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "source_outbound_asset_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    scrap_qualified = models.ForeignObject(
        "inventory.ScrapOrder",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "scrap_id", "disposal_type"),
        to_fields=("tenant_id", "id", "disposal_type"),
        related_name="+",
        null=False,
    )
    source_loss_item_qualified = models.ForeignObject(
        "inventory.ScrapItem",
        on_delete=models.DO_NOTHING,
        from_fields=(
            "tenant_id",
            "source_loss_item_id",
            "asset_card_id",
            "source_loss_type",
        ),
        to_fields=("tenant_id", "id", "asset_card_id", "disposal_type"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "scrap_items"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "id", "asset_card_id"),
            ("tenant_id", "id", "item_id"),
            ("tenant_id", "id", "asset_card_id", "disposal_type"),
        )


class AdjustmentOrder(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    order_no = models.TextField()
    type = models.TextField()
    status = models.TextField(db_default=RawSQL("'pending'::text", ()))
    applicant_id = models.BigIntegerField()
    approver_id = models.BigIntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    operation_id = models.BigIntegerField(null=True)
    self_approved = models.BooleanField(db_default=RawSQL("false", ()))
    allow_self_approval_snapshot = models.BooleanField(db_default=RawSQL("false", ()))
    reason_category = models.TextField()
    reason = models.TextField()
    decided_by = models.BigIntegerField(null=True)
    decided_at = models.DateTimeField(null=True)
    decision_reason = models.TextField(null=True)

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    applicant = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "applicant_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    approver = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "approver_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    operation = models.ForeignObject(
        "core.OperationRequest",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operation_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    decider = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "decided_by"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "adjustment_orders"
        unique_together = (
            ("tenant_id", "id"),
            ("tenant_id", "order_no"),
            ("tenant_id", "id", "type"),
        )


class AdjustmentItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    order_id = models.BigIntegerField()
    item_id = models.BigIntegerField()
    item_type = models.TextField()
    location_id = models.BigIntegerField()
    direction = models.TextField()
    quality = models.TextField()
    to_quality = models.TextField(null=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    observed_at = models.DateTimeField()
    book_qty = models.DecimalField(max_digits=14, decimal_places=3, null=True)
    counted_qty = models.DecimalField(max_digits=14, decimal_places=3, null=True)
    inventory_version = models.BigIntegerField(null=True)
    target_inventory_version = models.BigIntegerField(null=True)
    original_value = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    adjustment_type = models.TextField()

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    order = models.ForeignObject(
        "inventory.AdjustmentOrder",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "order_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    item = models.ForeignObject(
        "catalog.Item",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "item_id", "item_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    order_qualified = models.ForeignObject(
        "inventory.AdjustmentOrder",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "order_id", "adjustment_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )

    class Meta:
        managed = False
        db_table = "adjustment_items"
        unique_together = (("tenant_id", "id"), ("tenant_id", "id", "item_id"))


class StockLedger(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    item_id = models.BigIntegerField()
    item_type = models.TextField(db_default=RawSQL("'consumable'::text", ()))
    location_id = models.BigIntegerField()
    quality = models.TextField()
    direction = models.TextField()
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    balance_after = models.DecimalField(max_digits=14, decimal_places=3)
    inbound_item_id = models.BigIntegerField(null=True)
    outbound_item_id = models.BigIntegerField(null=True)
    scrap_item_id = models.BigIntegerField(null=True)
    adjustment_item_id = models.BigIntegerField(null=True)
    operator_id = models.BigIntegerField()
    operation_id = models.BigIntegerField()

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    item = models.ForeignObject(
        "catalog.Item",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "item_id", "item_type"),
        to_fields=("tenant_id", "id", "type"),
        related_name="+",
        null=False,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    inbound_item = models.ForeignObject(
        "inventory.InboundItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "inbound_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    outbound_item = models.ForeignObject(
        "inventory.OutboundItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "outbound_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    scrap_item = models.ForeignObject(
        "inventory.ScrapItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "scrap_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )
    operator = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operator_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    operation = models.ForeignObject(
        "core.OperationRequest",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operation_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    adjustment_item = models.ForeignObject(
        "inventory.AdjustmentItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "adjustment_item_id", "item_id"),
        to_fields=("tenant_id", "id", "item_id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "stock_ledger"
        unique_together = (("tenant_id", "id"),)


class AssetEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    created_at = models.DateTimeField(db_default=RawSQL("now()", ()), editable=False)
    asset_card_id = models.BigIntegerField()
    action = models.TextField()
    from_status = models.TextField(null=True)
    to_status = models.TextField()
    inbound_item_id = models.BigIntegerField(null=True)
    outbound_asset_id = models.BigIntegerField(null=True)
    scrap_item_id = models.BigIntegerField(null=True)
    adjustment_item_id = models.BigIntegerField(null=True)
    transfer_id = models.UUIDField(null=True)
    source_loss_item_id = models.BigIntegerField(null=True)
    location_id = models.BigIntegerField()
    responsible_user_id = models.BigIntegerField(null=True)
    operator_id = models.BigIntegerField()
    operation_id = models.BigIntegerField()
    reason = models.TextField(null=True)
    scrap_event_type = models.GeneratedField(
        expression=models.Case(
            models.When(action="scrap", then=models.Value("scrap")),
            models.When(action="loss", then=models.Value("loss")),
            models.When(action="found", then=models.Value("recovery")),
            default=None,
            output_field=models.TextField(),
        ),
        output_field=models.TextField(),
        db_persist=True,
        null=True,
    )
    source_loss_type = models.GeneratedField(
        expression=models.Value("loss"),
        output_field=models.TextField(),
        db_persist=True,
        null=True,
    )

    tenant = models.ForeignObject(
        "core.Tenant",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id",),
        to_fields=("id",),
        related_name="+",
        null=False,
    )
    asset_card = models.ForeignObject(
        "inventory.AssetCard",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "asset_card_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    inbound_item = models.ForeignObject(
        "inventory.InboundItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "inbound_item_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    outbound_asset = models.ForeignObject(
        "inventory.OutboundAsset",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "outbound_asset_id", "asset_card_id"),
        to_fields=("tenant_id", "id", "asset_card_id"),
        related_name="+",
        null=True,
    )
    scrap_item = models.ForeignObject(
        "inventory.ScrapItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "scrap_item_id", "asset_card_id"),
        to_fields=("tenant_id", "id", "asset_card_id"),
        related_name="+",
        null=True,
    )
    operator = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operator_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    operation = models.ForeignObject(
        "core.OperationRequest",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "operation_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    adjustment_item = models.ForeignObject(
        "inventory.AdjustmentItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "adjustment_item_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )
    source_loss_item = models.ForeignObject(
        "inventory.ScrapItem",
        on_delete=models.DO_NOTHING,
        from_fields=(
            "tenant_id",
            "source_loss_item_id",
            "asset_card_id",
            "source_loss_type",
        ),
        to_fields=("tenant_id", "id", "asset_card_id", "disposal_type"),
        related_name="+",
        null=True,
    )
    scrap_item_qualified = models.ForeignObject(
        "inventory.ScrapItem",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "scrap_item_id", "asset_card_id", "scrap_event_type"),
        to_fields=("tenant_id", "id", "asset_card_id", "disposal_type"),
        related_name="+",
        null=True,
    )
    location = models.ForeignObject(
        "catalog.Location",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "location_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=False,
    )
    responsible_user = models.ForeignObject(
        "core.User",
        on_delete=models.DO_NOTHING,
        from_fields=("tenant_id", "responsible_user_id"),
        to_fields=("tenant_id", "id"),
        related_name="+",
        null=True,
    )

    class Meta:
        managed = False
        db_table = "asset_events"
        unique_together = (("tenant_id", "id"),)
