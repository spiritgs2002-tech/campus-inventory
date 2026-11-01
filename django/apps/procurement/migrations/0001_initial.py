from django.db import migrations, models
from django.db.migrations.state import ModelState
from shared.db.operations import RunSQLFile


class CreateTableSQL(RunSQLFile):
    def state_forwards(self, app_label, state):
        state.add_model(
            ModelState(
                app_label,
                "Requisition",
                fields=[
                    ("id", models.BigAutoField(primary_key=True)),
                    ("tenant_id", models.BigIntegerField()),
                    ("order_no", models.TextField()),
                    ("applicant_id", models.BigIntegerField()),
                    ("applicant_name", models.TextField()),
                    ("applicant_no", models.TextField()),
                    ("item_id", models.BigIntegerField()),
                    ("qty", models.DecimalField(max_digits=14, decimal_places=3)),
                    ("reason", models.TextField()),
                    ("status", models.TextField(default="pending")),
                    ("approved_by", models.BigIntegerField(null=True)),
                    ("inbound_order_id", models.BigIntegerField(null=True)),
                    ("reject_reason", models.TextField(null=True)),
                    ("created_at", models.DateTimeField(auto_now_add=True)),
                    ("updated_at", models.DateTimeField(auto_now=True)),
                ],
                options={"managed": False, "db_table": "procurement_requisition"},
            )
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("catalog", "0002_add_item_qr")]
    operations = [
        CreateTableSQL(
            "p8b-procurement.sql",
            "27df0a65ed7b77e363fe5370ae2d431c54550e33e314d64df560a3d2305e0c0d",
        )
    ]
