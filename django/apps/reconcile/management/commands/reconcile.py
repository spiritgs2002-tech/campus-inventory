import json
from django.core.management.base import BaseCommand, CommandError
from apps.reconcile.services import run
from shared.errors import BusinessError


class Command(BaseCommand):
    help = "Run 13 read-only consistency checks using the ordinary runtime login"

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--sample-limit", type=int, default=20)

    def handle(self, *args, **options):
        try:
            result = run(sample_limit=options["sample_limit"])
        except BusinessError as error:
            raise CommandError(f"{error.code}: {error.message}") from None
        if options["json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
        else:
            for check in result["checks"]:
                self.stdout.write(f"{check['name']}: {check['count']}")
            self.stdout.write(
                f"Snapshot: {result['isolation']}; read_only={result['read_only']}"
            )
        if not result["ok"]:
            raise CommandError(
                "Reconciliation found inconsistencies; no data was changed"
            )
        if not options["json"]:
            self.stdout.write("Reconciliation passed: 13/13 checks, zero issues")
