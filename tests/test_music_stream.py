"""YouTube stream arguments must preserve the extractor's required headers."""

import shlex
import unittest
from unittest.mock import patch

try:
    from app.player import music_audio_source
except ImportError:  # Skip in a Python environment without the optional voice dependencies.
    music_audio_source = None


@unittest.skipIf(music_audio_source is None, "Discord voice dependencies are unavailable")
class MusicStreamTests(unittest.TestCase):
    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_passes_youtube_http_headers_to_ffmpeg(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "url": "https://example.invalid/audio",
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (example)",
                "Referer": "https://www.youtube.com/",
                "X-Unsafe": "do-not-forward",
            },
        }

        music_audio_source("https://www.youtube.com/watch?v=abc")

        args = shlex.split(ffmpeg.call_args.kwargs["before_options"])
        headers = args[args.index("-headers") + 1]
        self.assertIn("User-Agent: Mozilla/5.0 (example)\r\n", headers)
        self.assertIn("Referer: https://www.youtube.com/\r\n", headers)
        self.assertNotIn("X-Unsafe", headers)
        self.assertEqual(ffmpeg.call_args.args, ("https://example.invalid/audio",))


if __name__ == "__main__":
    unittest.main()
