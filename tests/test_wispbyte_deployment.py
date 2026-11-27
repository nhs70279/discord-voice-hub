"""Offline checks for the Wispbyte bot + web launcher."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import start_wispbyte


ROOT = Path(__file__).resolve().parents[1]


class WispbyteDeploymentTests(unittest.TestCase):
    def test_wispbyte_primary_port_priority(self):
        self.assertEqual(start_wispbyte.pick_port({"SERVER_PORT": "15351", "PORT": "8080"}), 15351)
        self.assertEqual(start_wispbyte.pick_port({"PORT": "9000"}), 9000)
        self.assertEqual(start_wispbyte.pick_port({}), 8080)
        for invalid in ("0", "65536", "not-a-number"):
            with self.assertRaises(ValueError):
                start_wispbyte.pick_port({"SERVER_PORT": invalid})

    def test_storage_is_private_and_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            environment = os.environ.copy()
            environment.pop("DATABASE_PATH", None)
            environment.pop("COOKIE_SECURE", None)
            with patch.dict(os.environ, environment, clear=True), patch.object(start_wispbyte, "DATA", root / "data"), patch.object(start_wispbyte, "RUNTIME", root / "runtime"):
                start_wispbyte.configure_storage()
                self.assertEqual(os.environ["DATABASE_PATH"], str(root / "data" / "voice_hub.sqlite3"))
                self.assertEqual(os.environ["COOKIE_SECURE"], "true")
                self.assertTrue((root / "runtime").is_dir())
                self.assertEqual((root / "data").stat().st_mode & 0o777, 0o700)

    def test_launcher_and_dependency_manifest(self):
        manifest = (ROOT / "requirements.wispbyte.txt").read_text(encoding="utf-8")
        self.assertIn("-r requirements.txt", manifest)
        self.assertIn("imageio-ffmpeg", manifest)
        runner = (ROOT / "start_wispbyte.py").read_text(encoding="utf-8")
        self.assertIn('host="0.0.0.0"', runner)
        self.assertIn("workers=1", runner)
        self.assertIn("sha256", runner)
        self.assertIn('"SERVER_PORT"', runner)

    def test_runtime_and_data_are_not_committed(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/", ignore)
        self.assertIn(".runtime/", ignore)


if __name__ == "__main__":
    unittest.main()
