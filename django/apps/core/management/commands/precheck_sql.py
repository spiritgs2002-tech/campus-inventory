import subprocess
import sys
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Run the authoritative v7 SQL static precheck without connecting to PostgreSQL"
    )
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "path", nargs="?", type=Path, default=settings.SQL_ROOT / "schema-v7.sql"
        )

    def handle(self, *args, **options):
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
