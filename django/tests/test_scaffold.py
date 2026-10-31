import tempfile
from pathlib import Path
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, RequestFactory
from django.http import HttpResponse
from shared.rbac.permission import require_role


class ScaffoldTests(SimpleTestCase):
    def test_expired_htmx_identity_redirects_the_whole_browser(self):
        request = RequestFactory().get("/catalog/items/", HTTP_HX_REQUEST="true")
        request.principal = None
        response = require_role("admin")(lambda request: HttpResponse())(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Redirect"], "/auth/login/")

    def test_home(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "P0")

    def test_liveness_and_csp(self):
        response = self.client.get("/health/live/")
        self.assertEqual(response.json(), {"status": "ok", "phase": "P0"})
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", response.headers["Content-Security-Policy"])

    def test_no_write_surface(self):
        self.assertEqual(self.client.post("/health/live/").status_code, 405)

    def test_precheck_baseline(self):
        call_command("precheck_sql")

    def test_precheck_rejects_broken_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.sql"
            path.write_text("CREATE FUNCTION broken() RETURNS void AS $\n")
            with self.assertRaises(CommandError):
                call_command("precheck_sql", str(path))
