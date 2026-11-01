from django.db import migrations, models
from django.db.migrations.state import ModelState
from shared.db.operations import RunSQLFile


class CreateTableSQL(RunSQLFile):
    def state_forwards(self, app_label, state):
        state.add_model(
            ModelState(
                app_label,
                "Notification",
                fields=[
                    ("id", models.BigAutoField(primary_key=True)),
                    ("tenant_id", models.BigIntegerField()),
                    ("recipient_user_id", models.BigIntegerField()),
                    ("recipient_name", models.TextField(null=True)),
                    ("type", models.TextField()),
                    ("title", models.TextField()),
                    ("body", models.TextField()),
                    ("ref_type", models.TextField(null=True)),
                    ("ref_id", models.BigIntegerField(null=True)),
                    ("is_read", models.BooleanField(default=False)),
                    ("created_at", models.DateTimeField(auto_now_add=True)),
                ],
                options={"managed": False, "db_table": "notifications_notification"},
            )
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("catalog", "0002_add_item_qr")]
    operations = [
        CreateTableSQL(
            "p8b-notifications.sql",
            "35a00f0b9a30509797401f6a3ba593f4e61c480d7ae072ceda222544243ebca4",
        )
    ]
