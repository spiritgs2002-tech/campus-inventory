"""Add repair storage with pinned SQL; mirror unmanaged fields without ORM DDL."""

from django.db import migrations, models
from django.db.migrations.state import ModelState
from shared.db.operations import RunSQLFile


class CreateRepairSQL(RunSQLFile):
    def state_forwards(self, app_label, state):
        state.add_model(
            ModelState(
                app_label,
                "RepairTicket",
                fields=[
                    ("id", models.BigAutoField(primary_key=True)),
                    ("tenant_id", models.BigIntegerField()),
                    ("ticket_no", models.TextField()),
                    ("reporter_user_id", models.BigIntegerField()),
                    ("reporter_name", models.TextField()),
                    ("reporter_no", models.TextField()),
                    ("target_type", models.TextField()),
                    ("target_id", models.BigIntegerField(null=True)),
                    ("description", models.TextField()),
                    ("photo_attachment_id", models.BigIntegerField(null=True)),
                    ("priority", models.TextField(default="normal")),
                    ("status", models.TextField(default="pending")),
                    ("assigned_to", models.BigIntegerField(null=True)),
                    ("reject_reason", models.TextField(null=True)),
                    ("created_at", models.DateTimeField(auto_now_add=True)),
                    ("updated_at", models.DateTimeField(auto_now=True)),
                    (
                        "asset_target_id",
                        models.GeneratedField(
                            expression=models.Case(
                                models.When(
                                    target_type="asset", then=models.F("target_id")
                                )
                            ),
                            output_field=models.BigIntegerField(),
                            db_persist=True,
                            null=True,
                        ),
                    ),
                    (
                        "item_target_id",
                        models.GeneratedField(
                            expression=models.Case(
                                models.When(
                                    target_type="item", then=models.F("target_id")
                                )
                            ),
                            output_field=models.BigIntegerField(),
                            db_persist=True,
                            null=True,
                        ),
                    ),
                    (
                        "location_target_id",
                        models.GeneratedField(
                            expression=models.Case(
                                models.When(
                                    target_type="location", then=models.F("target_id")
                                )
                            ),
                            output_field=models.BigIntegerField(),
                            db_persist=True,
                            null=True,
                        ),
                    ),
                ],
                options={"managed": False, "db_table": "repair_repairticket"},
            )
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("procurement", "0001_initial"), ("notifications", "0001_initial")]
    operations = [
        CreateRepairSQL(
            "p8c-repair.sql",
            "439c5a75f61e7027d8a8586ad8927ad4025a924e06492cac3d4a5f255a571e04",
        )
    ]
