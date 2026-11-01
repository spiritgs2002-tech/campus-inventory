from django.db import models


class Notification(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.BigIntegerField()
    recipient_user_id = models.BigIntegerField()
    recipient_name = models.TextField(null=True)
    type = models.TextField()
    title = models.TextField()
    body = models.TextField()
    ref_type = models.TextField(null=True)
    ref_id = models.BigIntegerField(null=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "notifications_notification"
