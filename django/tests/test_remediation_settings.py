"""Settings parsing for the anonymous-scan switch and proxy trust (remediation)."""

import json
import os
import subprocess
import sys
from pathlib import Path
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
PROBE = (
    "import json; from campus_inventory import settings as s; "
    "a = s.ANONYMOUS_SCAN_TENANTS; "
    "print(json.dumps({'anon': a if a == '*' else sorted(a), "
    "'proxy': list(getattr(s, 'SECURE_PROXY_SSL_HEADER', None) or [])}))"
)


def probe(**env):
    base = {k: v for k, v in os.environ.items() if k not in {
        "ANONYMOUS_SCAN_TENANTS", "TRUST_X_FORWARDED_PROTO"}}
    base.update(env)
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env=base,
        capture_output=True,
        text=True,
        timeout=60,
    )


class RemediationSettingsTests(SimpleTestCase):
    def test_defaults_keep_scan_and_do_not_trust_proxy(self):
        result = probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"anon": "*", "proxy": []})

    def test_switch_values(self):
        for raw, expected in [("", []), ("none", []), ("2,1", [1, 2])]:
            result = probe(ANONYMOUS_SCAN_TENANTS=raw)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["anon"], expected)
        for bad in ("abc", "0", "-1"):
            self.assertNotEqual(probe(ANONYMOUS_SCAN_TENANTS=bad).returncode, 0)

    def test_proxy_header_only_when_explicitly_trusted(self):
        result = probe(TRUST_X_FORWARDED_PROTO="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["proxy"], ["HTTP_X_FORWARDED_PROTO", "https"]
        )
