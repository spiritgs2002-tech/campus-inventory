from django.db import models


class Requisition(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    order_no = models.TextField()
    applicant_id = models.BigIntegerField()
    applicant_name = models.TextField()
    applicant_no = models.TextField()
    item_id = models.BigIntegerField()
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.TextField()
    status = models.TextField(default="pending")
    approved_by = models.BigIntegerField(null=True)
    inbound_order_id = models.BigIntegerField(null=True)
    reject_reason = models.TextField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = "procurement_requisition"
