"""Apply the versioned SQL baseline through Django's migration recorder."""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Apply the SQL-first migrations to a dedicated database (never fake/adopt existing tables)"

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        alias = options["database"]
        connection = connections[alias]
        if connection.vendor != "postgresql":
            raise CommandError("PostgreSQL 16 is required")
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            if int(cursor.fetchone()[0]) // 10000 != 16:
                raise CommandError("Use PostgreSQL major version 16")
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'django_migrations'"
            )
            tables = {row[0] for row in cursor.fetchall()}
        recorded = ("core", "0001_initial") in MigrationRecorder(
            connection
        ).applied_migrations()
        if tables and not recorded:
            raise CommandError(
                "Unrecorded schema exists: refusing to adopt or overwrite it. Use a fresh dedicated database or DBA-reviewed recovery."
            )
        call_command("precheck_sql", stdout=self.stdout, stderr=self.stderr)
        call_command(
            "migrate",
            database=alias,
            interactive=False,
            stdout=self.stdout,
            stderr=self.stderr,
        )
        call_command(
            "verify_schema", database=alias, stdout=self.stdout, stderr=self.stderr
        )
        call_command(
            "verify_runtime", database=alias, stdout=self.stdout, stderr=self.stderr
        )
