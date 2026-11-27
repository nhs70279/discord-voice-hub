"""Offline tests for the opt-in YouTube smoke probe."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import start_wispbyte
from scripts import probe_youtube


class YoutubeProbeTests(unittest.TestCase):
    def test_probe_is_disabled_without_live_provider(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(probe_youtube.run_probe())

    def test_probe_checks_media_in_ffmpeg_without_logging_stream_url(self):
        source = MagicMock()
        source.__enter__.return_value.extract_info.return_value = {
            "url": "https://video.example/secure?secret=do-not-log",
            "http_headers": {"User-Agent": "test-agent", "Authorization": "do-not-include"},
        }
        with patch.dict(os.environ, {"YOUTUBE_POT_READY": "1"}), \
            patch.object(probe_youtube.yt_dlp, "YoutubeDL", return_value=source), \
            patch.object(probe_youtube.shutil, "which", return_value="/bin/ffmpeg"), \
            patch.object(probe_youtube.subprocess, "run", return_value=MagicMock(returncode=0)) as proc:
            self.assertTrue(probe_youtube.run_probe())
        arguments = proc.call_args.args[0]
        self.assertIn("https://video.example/secure?secret=do-not-log", arguments)
        self.assertNotIn("Authorization", " ".join(arguments))
        self.assertIn("-headers", arguments)

    def test_probe_classifies_verification_without_printing_error_body(self):
        source = MagicMock()
        source.__enter__.return_value.extract_info.side_effect = probe_youtube.yt_dlp.utils.DownloadError(
            "Sign in to confirm you're not a bot: secret session data"
        )
        with patch.dict(os.environ, {"YOUTUBE_POT_READY": "1"}), \
            patch.object(probe_youtube.yt_dlp, "YoutubeDL", return_value=source), \
            self.assertLogs("voicehub.youtube_probe", "WARNING") as logs:
            self.assertFalse(probe_youtube.run_probe())
        self.assertIn("login verification", logs.output[0])
        self.assertNotIn("secret session", logs.output[0])

    def test_probe_runs_once_and_never_receives_discord_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "youtube-probe.requested").touch()
            process = MagicMock()
            with patch.dict(os.environ, {
                "YOUTUBE_POT_READY": "1", "DISCORD_TOKEN": "private-bot-token",
                "YOUTUBE_COOKIES_PATH": "private-cookie-file",
            }), patch.object(start_wispbyte, "DATA", data), \
                patch.object(start_wispbyte.subprocess, "Popen", return_value=process) as popen:
                self.assertIs(start_wispbyte.start_youtube_smoke_probe(), process)
                self.assertIsNone(start_wispbyte.start_youtube_smoke_probe())
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(environment["YOUTUBE_POT_READY"], "1")
            self.assertNotIn("DISCORD_TOKEN", environment)
            self.assertNotIn("YOUTUBE_COOKIES_PATH", environment)
            self.assertTrue((data / "youtube-probe.started").exists())


if __name__ == "__main__":
    unittest.main()
