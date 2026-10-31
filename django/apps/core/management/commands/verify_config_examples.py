import json
import os
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from shared.config_examples import HOST_KEY, verify_example_files


class Command(BaseCommand):
    help = "Validate database configuration examples and actual socket setting readback without connecting to PG"
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            result = verify_example_files(settings.BASE_DIR)
            sentinel = "/tmp/inventory-config-verification"
            env = {**os.environ, HOST_KEY.decode("ascii"): sentinel}
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json; from campus_inventory.settings import DATABASES; print(json.dumps(DATABASES['default']['HOST']))",
                ],
                cwd=settings.BASE_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if probe.returncode or json.loads(probe.stdout) != sentinel:
                raise ValueError(
                    "Actual settings readback did not preserve the configured socket path"
                )
            result["socket_override_readback"] = True
        except (OSError, ValueError, SyntaxError, subprocess.TimeoutExpired) as error:
            raise CommandError(
                f"Configuration example verification failed: {error}"
            ) from error
        self.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True))
        self.stdout.write(
            "Configuration example assertions passed; no database connection made"
        )
