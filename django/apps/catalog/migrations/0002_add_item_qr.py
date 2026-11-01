"""SQL-first expansion. Published P7 migration and SQL bytes stay immutable."""

from django.db import migrations
from shared.db.operations import AddItemQRSQL


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("catalog", "0001_initial"), ("core", "0003_runtime_access_guards")]
    operations = [
        AddItemQRSQL(
            "p8a-item-qr.sql",
            "c2984037239667a377f6678dcaa7c0c37497cf1ee1d023e9f511783b7727fa6d",
        )
    ]
