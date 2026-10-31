import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import SimpleTestCase
from shared.config_examples import HOST_KEY, LEGACY_KEY, verify_example_files


class ConfigurationExampleTests(SimpleTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "campus_inventory").mkdir()
        for name in (".env.example", "README.md", "campus_inventory/settings.py"):
            shutil.copyfile(settings.BASE_DIR / name, self.root / name)

    def test_current_examples_match_the_real_settings_key(self):
        result = verify_example_files(self.root)
        self.assertEqual(result["host_key_hex"], "44425f484f5354")
        self.assertFalse(result["legacy_key_present"])

    def test_original_misspelling_in_environment_example_is_rejected(self):
        path = self.root / ".env.example"
        path.write_bytes(path.read_bytes().replace(HOST_KEY + b"=", LEGACY_KEY + b"="))
        with self.assertRaises(ValueError):
            verify_example_files(self.root)

    def test_misspelling_in_readme_is_rejected(self):
        path = self.root / "README.md"
        path.write_bytes(path.read_bytes().replace(HOST_KEY + b"=", LEGACY_KEY + b"="))
        with self.assertRaises(ValueError):
            verify_example_files(self.root)

    def test_duplicate_host_assignment_is_rejected(self):
        path = self.root / ".env.example"
        path.write_bytes(path.read_bytes() + b"\n" + HOST_KEY + b"=/tmp\n")
        with self.assertRaises(ValueError):
            verify_example_files(self.root)

    def test_wrong_host_setting_key_is_rejected_even_if_default_masks_it(self):
        path = self.root / "campus_inventory/settings.py"
        path.write_bytes(path.read_bytes().replace(HOST_KEY, LEGACY_KEY))
        with self.assertRaises(ValueError):
            verify_example_files(self.root)
