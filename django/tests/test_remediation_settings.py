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


class ProxyTrustCheckTests(SimpleTestCase):
    def run_check(self, debug, env):
        from unittest.mock import patch
        from django.test import override_settings
        from apps.core.checks import proxy_trust_is_explicit

        with override_settings(DEBUG=debug), patch.dict(os.environ, env, clear=False):
            if "TRUST_X_FORWARDED_PROTO" not in env:
                os.environ.pop("TRUST_X_FORWARDED_PROTO", None)
            return [w.id for w in proxy_trust_is_explicit(None)]

    def test_warns_only_in_production_when_unset(self):
        self.assertEqual(self.run_check(False, {}), ["campus.W001"])
        self.assertEqual(self.run_check(False, {"TRUST_X_FORWARDED_PROTO": "false"}), [])
        self.assertEqual(self.run_check(False, {"TRUST_X_FORWARDED_PROTO": "true"}), [])
        self.assertEqual(self.run_check(True, {}), [])
