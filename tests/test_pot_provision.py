"""Provider provisioning stays opt-in and never forwards Discord credentials."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import provision_youtube_pot as pot


class PotProvisionTests(unittest.TestCase):
    def _locations(self, root: Path):
        repo = root / "data" / "bgutil-ytdlp-pot-provider"
        server = repo / "server"
        return patch.multiple(
            pot, ROOT=root, DATA=root / "data", MARKER=root / "data" / "youtube-pot.enabled",
            REPOSITORY=repo, SERVER=server, STAMP=server / ".lilin-installed-2.0.2",
        )

    def test_no_marker_never_runs_new_software(self):
        with tempfile.TemporaryDirectory() as folder, self._locations(Path(folder)), \
            patch.object(pot.subprocess, "run") as run:
            self.assertFalse(pot.provision())
            run.assert_not_called()

    def test_one_time_install_is_pinned_and_hides_bot_secrets(self):
        with tempfile.TemporaryDirectory() as folder, self._locations(Path(folder)):
            pot.DATA.mkdir()
            pot.MARKER.touch()
            deno = Path(folder) / "deno"
            deno.touch(mode=0o700)
            deno.chmod(0o700)
            commands = []

            def run(argv, **kwargs):
                commands.append((argv, kwargs))
                self.assertNotIn("DISCORD_TOKEN", kwargs["env"])
                self.assertNotIn("DISCORD_CLIENT_SECRET", kwargs["env"])
                self.assertNotIn("YOUTUBE_COOKIES_PATH", kwargs["env"])
                if "clone" in argv:
                    (pot.REPOSITORY / ".git").mkdir(parents=True)
                    pot.SERVER.mkdir(parents=True)
                elif "install" in argv:
                    (pot.SERVER / "node_modules").mkdir(parents=True)
                return subprocess.CompletedProcess(argv, 0, stdout=b"2.0.2\n")

            with patch.dict(os.environ, {
                "DISCORD_TOKEN": "hidden", "DISCORD_CLIENT_SECRET": "hidden",
                "YOUTUBE_COOKIES_PATH": "hidden", "PATH": str(Path(folder)),
            }), patch.object(pot.shutil, "which", side_effect=lambda name: str(deno) if name == "deno" else "/usr/bin/git"), \
                patch.object(pot.subprocess, "run", side_effect=run):
                self.assertTrue(pot.provision())
                self.assertTrue(pot.STAMP.is_file())
                self.assertIn("--branch", commands[0][0])
                self.assertIn("2.0.2", commands[0][0])
                self.assertIn("--exact-match", commands[1][0])
                self.assertIn("--allow-scripts=npm:canvas", commands[2][0])
                self.assertTrue(pot.provision())
                self.assertEqual(len(commands), 3)

    def test_mismatched_release_never_executes_npm_scripts(self):
        with tempfile.TemporaryDirectory() as folder, self._locations(Path(folder)):
            pot.DATA.mkdir()
            pot.MARKER.touch()
            (pot.REPOSITORY / ".git").mkdir(parents=True)
            pot.SERVER.mkdir()
            deno = Path(folder) / "deno"
            deno.touch(mode=0o700)
            deno.chmod(0o700)
            with patch.object(pot.shutil, "which", side_effect=lambda name: str(deno) if name == "deno" else "/usr/bin/git"), \
                patch.object(pot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"1.0.0\n")) as run:
                self.assertFalse(pot.provision())
                self.assertFalse(pot.STAMP.exists())
                self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
