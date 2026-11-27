"""YouTube stream arguments must preserve the extractor's required headers."""

import shlex
import unittest
from unittest.mock import patch

try:
    from app.player import MusicSourceUnavailable, discover_tracks, music_audio_source, valid_query
    from yt_dlp.utils import DownloadError
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

    def test_soundcloud_https_links_are_allowed_but_arbitrary_urls_are_not(self):
        self.assertTrue(valid_query("https://soundcloud.com/singer/song"))
        self.assertTrue(valid_query("https://on.soundcloud.com/shortlink"))
        self.assertFalse(valid_query("https://soundcloud.com.attacker.invalid/song"))
        self.assertFalse(valid_query("http://soundcloud.com/singer/song"))

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_discovers_soundcloud_tracks(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [{"title": "Artist - Song", "webpage_url": "https://soundcloud.com/artist/song"}]
        }
        self.assertEqual(discover_tracks("https://soundcloud.com/artist/song"), [
            {"title": "Artist - Song", "url": "https://soundcloud.com/artist/song"}
        ])

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_keyword_search_uses_soundcloud_when_youtube_blocks_discovery(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = [
            DownloadError("Sign in to confirm you're not a bot"),
            {"entries": [{"title": "Ed Sheeran - Shape of You", "webpage_url": "https://soundcloud.com/ed/song"}]},
        ]
        self.assertEqual(discover_tracks("Ed Sheeran Shape of You"), [
            {"title": "Ed Sheeran - Shape of You", "url": "https://soundcloud.com/ed/song"}
        ])

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_empty_youtube_search_also_checks_soundcloud(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = [
            None,
            {"entries": [{"title": "Artist - Song", "webpage_url": "https://soundcloud.com/artist/song"}]},
        ]
        self.assertEqual(discover_tracks("Artist Song"), [
            {"title": "Artist - Song", "url": "https://soundcloud.com/artist/song"}
        ])

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_youtube_signin_error_uses_close_soundcloud_match(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = [
            DownloadError("Sign in to confirm you're not a bot"),
            {"entries": [
                {"title": "Unrelated artist", "webpage_url": "https://soundcloud.com/a/wrong"},
                {"title": "Ed Sheeran - Shape of You", "webpage_url": "https://soundcloud.com/ed/shape-of-you"},
            ]},
            {"url": "https://stream.example.invalid/song", "http_headers": {}},
        ]
        source, fallback_title = music_audio_source(
            "https://www.youtube.com/watch?v=abc", "Shape of You - Ed Sheeran [Official MV]"
        )
        self.assertIs(source, ffmpeg.return_value)
        self.assertEqual(fallback_title, "Ed Sheeran - Shape of You")
        self.assertEqual(ffmpeg.call_args.args, ("https://stream.example.invalid/song",))

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_does_not_play_unrelated_or_remixed_soundcloud_results(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = [
            DownloadError("Sign in to confirm you're not a bot"),
            {"entries": [
                {"title": "Something else", "webpage_url": "https://soundcloud.com/a/wrong"},
                {"title": "Shape of You - Ed Sheeran Remix", "webpage_url": "https://soundcloud.com/b/remix"},
            ]},
        ]
        with self.assertRaisesRegex(MusicSourceUnavailable, "YouTube"):
            music_audio_source("https://www.youtube.com/watch?v=abc", "Shape of You - Ed Sheeran")
        ffmpeg.assert_not_called()

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_other_youtube_errors_are_not_silently_replaced(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = DownloadError("Video unavailable")
        with self.assertRaises(DownloadError):
            music_audio_source("https://www.youtube.com/watch?v=abc", "Song")
        self.assertEqual(youtube_dl.call_count, 1)


if __name__ == "__main__":
    unittest.main()
