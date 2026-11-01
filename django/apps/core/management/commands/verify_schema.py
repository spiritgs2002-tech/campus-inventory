from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Assert G0 counts on the dedicated public schema, excluding Django migration metadata"

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        with connections[options["database"]].cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name NOT LIKE 'django_%'"
            )
            tables = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"
            )
            functions = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal"
            )
            triggers = cursor.fetchone()[0]
        counts = (tables, functions, triggers)
        if counts != (30, 14, 32):
            raise CommandError(
                f"G0 mismatch: tables/functions/triggers={counts}, expected (30, 14, 32)"
            )
        self.stdout.write("G0 passed: 30 business tables / 14 functions / 32 triggers")
