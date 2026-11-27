"""Offline checks for the Fly starter configuration and SQLite backups."""

import os
import sqlite3
import tempfile
import tomllib
import unittest
from pathlib import Path

from app import db
from app.backup import create_backup


ROOT = Path(__file__).resolve().parents[1]


class FlyConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))

    def test_machine_is_always_on_and_single_process(self):
        self.assertEqual(self.config["http_service"]["auto_stop_machines"], "off")
        self.assertTrue(self.config["http_service"]["auto_start_machines"])
        self.assertEqual(self.config["restart"][0]["policy"], "always")
        self.assertEqual(self.config["http_service"]["internal_port"], 8080)
        self.assertEqual(self.config["vm"][0]["size"], "shared-cpu-1x")
        self.assertEqual(self.config["vm"][0]["memory"], "512mb")
        self.assertNotIn("processes", self.config)

    def test_persistent_database_and_https(self):
        # Fly Launch generates [[mounts]], while the starter used [mounts].
        mounts = self.config["mounts"]
        if isinstance(mounts, list):
            mounts = mounts[0]
        self.assertEqual(mounts["source"], "voicehub_data")
        self.assertEqual(mounts["destination"], "/data")
        self.assertEqual(self.config["env"]["DATABASE_PATH"], "/data/voice_hub.sqlite3")
        self.assertEqual(self.config["env"]["COOKIE_SECURE"], "true")
        self.assertTrue(self.config["http_service"]["force_https"])
        self.assertEqual(self.config["http_service"]["checks"][0]["path"], "/healthz")

    def test_dockerignore_keeps_secrets_out(self):
        excludes = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", excludes)
        self.assertIn(".env.*", excludes)
        self.assertIn("*.sqlite3", excludes)


class BackupTests(unittest.TestCase):
    def test_creates_consistent_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            original = os.environ.get("DATABASE_PATH")
            os.environ["DATABASE_PATH"] = str(Path(temp) / "voice.sqlite3")
            try:
                db.initialize()
                db.set_voice("user", db.DEFAULT_VOICE)
                destination = Path(temp) / "backups" / "copy.sqlite3"
                self.assertEqual(create_backup(destination), destination)
                with sqlite3.connect(destination) as connection:
                    voice = connection.execute("SELECT voice FROM preferences WHERE user_id='user'").fetchone()
                self.assertEqual(voice[0], db.DEFAULT_VOICE)
                with self.assertRaises(FileExistsError):
                    create_backup(destination)
                with self.assertRaises(ValueError):
                    create_backup(Path(os.environ["DATABASE_PATH"]))
            finally:
                if original is None:
                    os.environ.pop("DATABASE_PATH", None)
                else:
                    os.environ["DATABASE_PATH"] = original
