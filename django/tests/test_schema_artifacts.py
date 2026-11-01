import hashlib
from io import StringIO
from pathlib import Path
import tempfile

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from scripts.sync_schema_snapshot import inspect_snapshot, refresh_header


class SchemaArtifactTests(SimpleTestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory(prefix="inventory-schema-proof-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        app = root / "django"
        (app / "sql").mkdir(parents=True)
        source = root / "schema-v7.sql"
        snapshot = app / "sql/schema-v7-current.sql"
        raw = (settings.SQL_ROOT / "schema-v7-current.sql").read_bytes()
        source.write_bytes(raw)
        snapshot.write_bytes(raw)
        return app, source, snapshot

    def test_current_artifact_and_default_precheck_verify_the_fingerprint(self):
        result = inspect_snapshot(settings.BASE_DIR)
        self.assertTrue(result["fingerprint_valid"])
        self.assertTrue(result["snapshot_matches"])
        output = StringIO()
        call_command("precheck_sql", stdout=output)
        self.assertIn(result["body_sha256"], output.getvalue())

    def test_stale_digest_line_count_or_changed_body_is_rejected(self):
        for change in ("digest", "lines", "body"):
            with self.subTest(change=change):
                app, source, snapshot = self.fixture()
                raw = source.read_bytes()
                _, digest, lines = refresh_header(raw)
                if change == "digest":
                    raw = raw.replace(digest.encode(), b"0" * 64, 1)
                elif change == "lines":
                    raw = raw.replace(
                        f"SQL 正文行数: {lines}".encode(),
                        f"SQL 正文行数: {lines + 1}".encode(),
                        1,
                    )
                else:
                    raw += b"-- deliberately changed body\n"
                source.write_bytes(raw)
                snapshot.write_bytes(raw)
                with self.assertRaisesMessage(ValueError, "SCHEMA_FINGERPRINT_STALE"):
                    inspect_snapshot(app)

    def test_snapshot_drift_is_rejected_without_changing_either_file(self):
        app, source, snapshot = self.fixture()
        snapshot.write_bytes(snapshot.read_bytes() + b"-- drift\n")
        before = source.read_bytes(), snapshot.read_bytes()
        with self.assertRaisesMessage(ValueError, "SCHEMA_SNAPSHOT_DRIFT"):
            inspect_snapshot(app)
        self.assertEqual(before, (source.read_bytes(), snapshot.read_bytes()))

    def test_explicit_sync_preserves_body_and_published_migration_artifacts(self):
        app, source, snapshot = self.fixture()
        frozen = app / "sql/schema-v7.sql"
        delta = app / "sql/p8a-item-qr.sql"
        for target in (frozen, delta):
            target.write_bytes((settings.SQL_ROOT / target.name).read_bytes())
        immutable = {target: target.read_bytes() for target in (frozen, delta)}
        changed = source.read_bytes() + b"-- next schema review\n"
        source.write_bytes(changed)
        result = inspect_snapshot(app, write=True)
        expected, digest, _ = refresh_header(changed)
        self.assertEqual(source.read_bytes(), expected)
        self.assertEqual(snapshot.read_bytes(), expected)
        self.assertEqual(result["body_sha256"], digest)
        self.assertEqual(
            expected[expected.index(b"\nBEGIN;") :],
            changed[changed.index(b"\nBEGIN;") :],
        )
        for target, original in immutable.items():
            self.assertEqual(target.read_bytes(), original)
        self.assertEqual(
            hashlib.sha256(frozen.read_bytes()).hexdigest(),
            "2cc1062a3386ed9815fce2fbb60b05df8d3b5827d70fec9a586b5543ac367f82",
        )

    def test_packaged_application_checks_snapshot_but_cannot_rewrite_without_source(
        self,
    ):
        app, source, snapshot = self.fixture()
        source.unlink()
        self.assertIsNone(inspect_snapshot(app)["authoritative_source"])
        with self.assertRaisesMessage(ValueError, "SCHEMA_SOURCE_MISSING"):
            inspect_snapshot(app, write=True)
        snapshot.write_bytes(snapshot.read_bytes() + b"-- invalid\n")
        with self.assertRaisesMessage(ValueError, "SCHEMA_FINGERPRINT_STALE"):
            inspect_snapshot(app)

    def test_begin_mentioned_in_a_comment_is_not_the_sql_body(self):
        app, source, _ = self.fixture()
        raw = source.read_bytes()
        self.assertLess(raw.index(b"BEGIN"), raw.index(b"\nBEGIN;"))
        _, digest, _ = refresh_header(raw)
        self.assertEqual(
            digest, hashlib.sha256(raw[raw.index(b"\nBEGIN;") + 1 :]).hexdigest()
        )
        with self.assertRaisesMessage(ValueError, "SCHEMA_BODY_MISSING"):
            refresh_header(b"-- BEGIN; in a comment is not a statement\n")

    def test_missing_or_duplicate_fingerprint_metadata_is_rejected(self):
        raw = (settings.SQL_ROOT / "schema-v7-current.sql").read_bytes()
        for changed in (
            raw.replace("SQL 正文行数:".encode(), b"removed:", 1),
            "-- SQL 正文行数: 0\n".encode() + raw,
        ):
            with self.subTest(header=changed[:80]):
                with self.assertRaisesMessage(ValueError, "SCHEMA_HEADER_INVALID"):
                    refresh_header(changed)

    def test_default_management_precheck_rejects_drift_before_sql_execution(self):
        app, _, snapshot = self.fixture()
        snapshot.write_bytes(snapshot.read_bytes() + b"-- drift\n")
        with override_settings(BASE_DIR=app, SQL_ROOT=app / "sql"):
            with self.assertRaisesMessage(CommandError, "SCHEMA_SNAPSHOT_DRIFT"):
                call_command("precheck_sql", stdout=StringIO())
