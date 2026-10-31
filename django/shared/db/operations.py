"""SQL-first migration operation: execute a pinned file without splitting its body."""

import hashlib
from pathlib import Path
from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


class RunSQLFile(migrations.RunSQL):
    def __init__(self, filename, sha256):
        self.filename = filename
        self.sha256 = sha256
        super().__init__(sql="")

    def deconstruct(self):
        return (self.__class__.__qualname__, [self.filename, self.sha256], {})

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        content = (SQL_DIR / self.filename).read_bytes()
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise RuntimeError(f"SQL artifact checksum mismatch: {self.filename}")
        if schema_editor.connection.in_atomic_block:
            raise RuntimeError(
                "SQL files own BEGIN/COMMIT; migration must use atomic=False"
            )
        # A one-element list makes RunSQL send the complete, unmodified file.
        self.sql = [(content.decode("utf-8"), None)]
        try:
            super().database_forwards(app_label, schema_editor, from_state, to_state)
        except Exception:
            schema_editor.connection.rollback()
            raise

    def database_backwards(self, *args, **kwargs):
        raise IrreversibleError(
            "Baseline has no destructive downgrade; restore a validated backup"
        )

    def describe(self):
        return f"Apply pinned SQL file {self.filename}"
