from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, DatabaseError


class Command(BaseCommand):
    help = "Execute the authoritative runtime ACL assertions (read only)"

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        try:
            with connections[options["database"]].cursor() as cursor:
                cursor.execute(
                    (settings.SQL_ROOT / "verify-runtime.sql").read_text(), params=None
                )
                while cursor.description is None and cursor.nextset():
                    pass
                for row in cursor.fetchall():
                    self.stdout.write(str(row[0]))
        except DatabaseError as error:
            raise CommandError(f"Runtime ACL verification failed: {error}") from error
