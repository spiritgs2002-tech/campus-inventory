"""Unmanaged mapping; handwritten SQL owns storage and tenant constraints."""

from django.db import models


class RepairTicket(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    ticket_no = models.TextField()
    reporter_user_id = models.BigIntegerField()
    reporter_name = models.TextField()
    reporter_no = models.TextField()
    target_type = models.TextField()
    target_id = models.BigIntegerField(null=True)
    description = models.TextField()
    photo_attachment_id = models.BigIntegerField(null=True)
    priority = models.TextField(default="normal")
    status = models.TextField(default="pending")
    assigned_to = models.BigIntegerField(null=True)
    reject_reason = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    asset_target_id = models.GeneratedField(
        expression=models.Case(
            models.When(target_type="asset", then=models.F("target_id"))
        ),
        output_field=models.BigIntegerField(),
        db_persist=True,
        null=True,
    )
    item_target_id = models.GeneratedField(
        expression=models.Case(
            models.When(target_type="item", then=models.F("target_id"))
        ),
        output_field=models.BigIntegerField(),
        db_persist=True,
        null=True,
    )
    location_target_id = models.GeneratedField(
        expression=models.Case(
            models.When(target_type="location", then=models.F("target_id"))
        ),
        output_field=models.BigIntegerField(),
        db_persist=True,
        null=True,
    )

    class Meta:
        managed = False
        db_table = "repair_repairticket"
