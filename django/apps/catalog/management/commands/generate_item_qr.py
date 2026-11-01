import json
from django.core.management.base import BaseCommand, CommandError
from apps.inventory.qr import assign_tokens
from apps.reconcile import maintenance as ops
from shared.errors import BusinessError


class Command(BaseCommand):
    help = "Generate missing consumable QR tokens under the configured operations admin"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--idempotency-key")
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tid, dry = options["tenant_id"], options["dry_run"]

        def work(actor):
            result = assign_tokens(tid, options["batch_size"], dry_run=dry)
            if not dry:
                ops.audit(tid, actor, "catalog.item_qr", result)
            return result

        try:
            result, replay = ops.command(
                tid,
                "catalog.item_qr",
                options["idempotency_key"],
                {"batch_size": options["batch_size"]},
                work,
                dry_run=dry,
            )
        except BusinessError as error:
            raise CommandError(f"{error.code}: {error.message}") from None
        self.stdout.write(
            json.dumps({**result, "replayed": replay}, ensure_ascii=False)
        )
