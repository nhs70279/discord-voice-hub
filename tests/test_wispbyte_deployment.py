"""Offline checks for the Wispbyte bot + web launcher."""

import os
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

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

    def test_tunnel_is_opt_in_and_not_given_token_in_command_line(self):
        with patch.dict(os.environ, {"CLOUDFLARE_TUNNEL_TOKEN": "private-test-token"}):
            process = MagicMock()
            with patch.object(start_wispbyte.subprocess, "Popen", return_value=process) as popen:
                self.assertIs(start_wispbyte.start_cloudflare_tunnel(Path("/opt/cloudflared")), process)
            argv = popen.call_args.args[0]
            self.assertEqual(argv, ["/opt/cloudflared", "tunnel", "--protocol", "quic", "run"])
            self.assertNotIn("private-test-token", " ".join(argv))
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(environment["TUNNEL_TOKEN"], "private-test-token")
            self.assertNotIn("CLOUDFLARE_TUNNEL_TOKEN", environment)

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(start_wispbyte.configure_cloudflared())

    def test_pot_provider_is_opt_in(self):
        with patch.dict(os.environ, {"YOUTUBE_POT_READY": "1"}, clear=True):
            self.assertIsNone(start_wispbyte.start_youtube_pot_provider())
            self.assertNotIn("YOUTUBE_POT_READY", os.environ)

    def test_pot_provider_does_not_receive_bot_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder) / "server"
            (server / "node_modules").mkdir(parents=True)
            (server / "src").mkdir()
            (server / "src" / "main.ts").write_text("", encoding="utf-8")
            process = MagicMock()
            process.poll.return_value = None
            with patch.dict(os.environ, {
                "YOUTUBE_POT_SERVER_HOME": str(server),
                "YOUTUBE_POT_READY": "", "DISCORD_TOKEN": "sensitive-discord-test-token",
                "DISCORD_CLIENT_SECRET": "sensitive-oauth-test-secret",
                "YOUTUBE_COOKIES_PATH": "sensitive-cookie-path",
            }), patch.object(start_wispbyte.shutil, "which", return_value="/opt/deno"), \
                patch.object(start_wispbyte.subprocess, "Popen", return_value=process) as popen, \
                patch.object(start_wispbyte.socket, "create_connection"):
                self.assertIs(start_wispbyte.start_youtube_pot_provider(), process)
                self.assertEqual(os.environ["YOUTUBE_POT_READY"], "1")

            arguments = popen.call_args.args[0]
            self.assertIn("--host", arguments)
            self.assertEqual(arguments[arguments.index("--host") + 1], "127.0.0.1")
            sanitized = popen.call_args.kwargs["env"]
            self.assertNotIn("DISCORD_TOKEN", sanitized)
            self.assertNotIn("DISCORD_CLIENT_SECRET", sanitized)
            self.assertNotIn("YOUTUBE_COOKIES_PATH", sanitized)

    def test_cloudflared_binary_download_is_checksum_verified(self):
        executable_bytes = b"test cloudflared executable"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def download(_url, target):
                Path(target).write_bytes(executable_bytes)

            with patch.dict(os.environ, {"CLOUDFLARE_TUNNEL_TOKEN": "private-test-token"}), \
                patch.object(start_wispbyte, "RUNTIME", root), \
                patch.object(start_wispbyte.shutil, "which", return_value=None), \
                patch.object(start_wispbyte.urllib.request, "urlretrieve", side_effect=download), \
                patch.object(start_wispbyte, "CLOUDFLARED_SHA256", hashlib.sha256(executable_bytes).hexdigest()):
                binary = start_wispbyte.configure_cloudflared()
                self.assertEqual(binary, root / "cloudflared")
                self.assertTrue(binary.stat().st_mode & 0o100)

            with patch.dict(os.environ, {"CLOUDFLARE_TUNNEL_TOKEN": "private-test-token"}), \
                patch.object(start_wispbyte, "RUNTIME", root), \
                patch.object(start_wispbyte.shutil, "which", return_value=None), \
                patch.object(start_wispbyte.urllib.request, "urlretrieve", side_effect=download), \
                patch.object(start_wispbyte, "CLOUDFLARED_SHA256", "0" * 64):
                binary.unlink()
                self.assertIsNone(start_wispbyte.configure_cloudflared())
                self.assertFalse(binary.exists())


if __name__ == "__main__":
    unittest.main()
