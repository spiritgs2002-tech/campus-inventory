import subprocess
import sys
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from scripts.sync_schema_snapshot import inspect_snapshot


class Command(BaseCommand):
    help = (
        "Run the authoritative v7 SQL static precheck without connecting to PostgreSQL"
    )
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            type=Path,
            default=settings.SQL_ROOT / "schema-v7-current.sql",
        )

    def handle(self, *args, **options):
        if options["path"].resolve() == (
            settings.SQL_ROOT / "schema-v7-current.sql"
        ).resolve():
            try:
                result = inspect_snapshot(settings.BASE_DIR)
            except (OSError, ValueError) as error:
                raise CommandError(str(error)) from error
            self.stdout.write(
                f"SQL fingerprint: {result['body_sha256']}; "
                f"body lines: {result['body_lines']}; current snapshot verified"
            )
        result = subprocess.run(
            [
                sys.executable,
                str(settings.SQL_ROOT / "precheck-sql.py"),
                str(options["path"]),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.stdout.write(result.stdout)
        if result.returncode:
            raise CommandError(
                result.stderr or "SQL precheck failed", returncode=result.returncode
            )
