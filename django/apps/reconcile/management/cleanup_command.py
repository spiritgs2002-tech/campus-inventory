import json
from django.core.management.base import BaseCommand, CommandError
from shared.errors import BusinessError


class CleanupCommand(BaseCommand):
    kind = None
    help = "Scoped, audited maintenance; explicit tenant and idempotency key required"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--idempotency-key")
        parser.add_argument("--batch-size", type=int, default=25)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        from apps.attachments.cleanup import cleanup_evidence, cleanup_orphans
        from apps.reconcile.maintenance import cleanup_sessions, cleanup_responses

        functions = {
            "sessions": cleanup_sessions,
            "responses": cleanup_responses,
            "evidence": cleanup_evidence,
            "orphans": cleanup_orphans,
        }
        try:
            result = functions[self.kind](
                options["tenant_id"],
                options["idempotency_key"],
                batch_size=options["batch_size"],
                dry_run=options["dry_run"],
            )
        except BusinessError as error:
            raise CommandError(f"{error.code}: {error.message}") from None
        if isinstance(result, tuple):
            result, replay = result
            result = {**result, "replayed": replay}
        self.stdout.write(json.dumps(result, ensure_ascii=False, default=str))
        if not result.get("ok", True):
            raise CommandError("部分对象未清理，墓碑保留；请使用原幂等键重试")
