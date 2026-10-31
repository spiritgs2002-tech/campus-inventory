from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.db.migrations.exceptions import IrreversibleError
from django.test import SimpleTestCase

from shared.db.operations import RunSQLFile


class SQLFileOperationTests(SimpleTestCase):
    def test_whole_file_is_sent_as_one_statement_without_rewriting(self):
        sql = b"BEGIN;\n-- keep comment\nSELECT '100%';\nCOMMIT;\n"
        with TemporaryDirectory() as directory:
            Path(directory, "sample.sql").write_bytes(sql)
            with patch("shared.db.operations.SQL_DIR", Path(directory)):
                operation = RunSQLFile("sample.sql", sha256(sql).hexdigest())
                editor = Mock()
                editor.connection.in_atomic_block = False
                operation.database_forwards("core", editor, None, None)
        editor.execute.assert_called_once_with(sql.decode(), params=None)

    def test_changed_artifact_is_rejected_before_execution(self):
        with TemporaryDirectory() as directory:
            Path(directory, "sample.sql").write_text("SELECT 1")
            with patch("shared.db.operations.SQL_DIR", Path(directory)):
                editor = Mock()
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    RunSQLFile("sample.sql", "incorrect").database_forwards(
                        "core", editor, None, None
                    )
                editor.execute.assert_not_called()

    def test_baseline_cannot_be_destructively_reversed(self):
        with self.assertRaises(IrreversibleError):
            RunSQLFile("schema-v7.sql", "unused").database_backwards()
