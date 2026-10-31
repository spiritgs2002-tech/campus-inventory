from pathlib import Path
import tempfile
from django.conf import settings
from django.test import SimpleTestCase
from scripts.verify_ci_delivery import inspect_delivery


class CIDeliveryTests(SimpleTestCase):
    def layout(self, relative="inventory-v4/django"):
        directory = tempfile.TemporaryDirectory(
            prefix="inventory-ci-layout-", dir="/tmp"
        )
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        app = root / relative
        (app / "docs/ci").mkdir(parents=True)
        (app / "manage.py").touch()
        template = app / "docs/ci/ci.yml.template"
        template.write_bytes(
            (settings.BASE_DIR / "docs/ci/ci.yml.template").read_bytes()
        )
        workflow = root / ".github/workflows/ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_bytes(template.read_bytes())
        return root, app, workflow, template

    def test_three_supported_repository_layouts(self):
        for relative in [".", "django", "inventory-v4/django"]:
            with self.subTest(layout=relative):
                root, app, workflow, _ = self.layout(relative)
                result = inspect_delivery(root)
                self.assertEqual(result["application_root"], str(app.resolve()))
                self.assertEqual(result["workflow"], str(workflow.resolve()))
                self.assertTrue(result["explicit_test_suite"])

    def test_missing_root_workflow_is_rejected_even_if_nested_exists(self):
        root, app, workflow, _ = self.layout()
        nested = app / ".github/workflows/ci.yml"
        nested.parent.mkdir(parents=True)
        workflow.replace(nested)
        with self.assertRaisesMessage(ValueError, "CI_WORKFLOW_MISSING"):
            inspect_delivery(root)

    def test_duplicate_nested_workflow_is_rejected(self):
        root, app, workflow, _ = self.layout()
        nested = app / ".github/workflows/ci.yml"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(workflow.read_bytes())
        with self.assertRaisesMessage(ValueError, "CI_DUPLICATE_WORKFLOW"):
            inspect_delivery(root)

    def test_portable_copy_drift_is_rejected(self):
        root, _, workflow, _ = self.layout()
        workflow.write_text(workflow.read_text() + "\n# modified\n")
        with self.assertRaisesMessage(ValueError, "CI_TEMPLATE_DRIFT"):
            inspect_delivery(root)

    def test_missing_phase_or_suite_is_rejected_even_if_copies_match(self):
        for text, message in [
            ("scripts/verify_p6.py", "CI_PHASE_MISSING"),
            ("manage.py test tests --no-input", "CI_TEST_SUITE_MISSING"),
        ]:
            with self.subTest(removed=text):
                root, _, workflow, template = self.layout()
                changed = workflow.read_text().replace(text, "removed")
                workflow.write_text(changed)
                template.write_text(changed)
                with self.assertRaisesMessage(ValueError, message):
                    inspect_delivery(root)
