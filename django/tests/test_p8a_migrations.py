import hashlib
from importlib import import_module
from django.conf import settings
from django.db import migrations
from django.test import SimpleTestCase


class P8aMigrationContractTests(SimpleTestCase):
    def test_incremental_sql_matches_the_authoritative_schema_chapter(self):
        current = (settings.SQL_ROOT / "schema-v7-current.sql").read_text()
        start = current.index("-- P8A-ITEM-QR-BEGIN")
        end = current.index("-- P8A-ITEM-QR-END") + len("-- P8A-ITEM-QR-END\n")
        delta = (settings.SQL_ROOT / "p8a-item-qr.sql").read_bytes()
        self.assertEqual(
            delta, ("BEGIN;\n" + current[start:end] + "COMMIT;\n").encode()
        )
        operation = import_module(
            "apps.catalog.migrations.0002_add_item_qr"
        ).Migration.operations[0]
        self.assertIsInstance(operation, migrations.RunSQL)
        self.assertEqual(operation.sha256, hashlib.sha256(delta).hexdigest())

    def test_p7_baseline_is_immutable_and_p8a_snapshot_is_separate(self):
        legacy = (settings.SQL_ROOT / "schema-v7.sql").read_bytes()
        self.assertEqual(
            hashlib.sha256(legacy).hexdigest(),
            "2cc1062a3386ed9815fce2fbb60b05df8d3b5827d70fec9a586b5543ac367f82",
        )
        self.assertNotEqual(
            legacy, (settings.SQL_ROOT / "schema-v7-current.sql").read_bytes()
        )
        current = (settings.SQL_ROOT / "schema-v7-current.sql").read_bytes()
        self.assertEqual(
            current[current.index(b"BEGIN;") : current.index(b"-- P8A-ITEM-QR-BEGIN")],
            legacy[legacy.index(b"BEGIN;") : -len(b"COMMIT;\n")],
        )
        source = settings.BASE_DIR.parent / "schema-v7.sql"
        if source.is_file():
            self.assertEqual(
                source.read_bytes(),
                (settings.SQL_ROOT / "schema-v7-current.sql").read_bytes(),
            )
