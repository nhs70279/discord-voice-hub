"""YouTube stream arguments must preserve the extractor's required headers."""

import shlex
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import app.player as player_module
    from app.player import (MusicSourceUnavailable, discover_tracks, downloaded_youtube_source,
                            music_audio_source, valid_query)
    from yt_dlp.utils import DownloadError
except ImportError:  # Skip in a Python environment without the optional voice dependencies.
    music_audio_source = None


@unittest.skipIf(music_audio_source is None, "Discord voice dependencies are unavailable")
class MusicStreamTests(unittest.TestCase):
    def setUp(self):
        # Bot challenges are shared between guilds in production, but tests
        # should not inherit each other's temporary cooldown.
        old_until = player_module._youtube_challenge_until
        player_module._youtube_challenge_until = 0.0
        self.addCleanup(setattr, player_module, "_youtube_challenge_until", old_until)
        env_patch = patch.dict(os.environ, {"YOUTUBE_PROXY_URL": "", "YOUTUBE_POT_READY": ""})
        env_patch.start()
        self.addCleanup(env_patch.stop)

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
    def test_prefers_alternate_youtube_clients_before_soundcloud(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = [
            DownloadError("HTTP Error 403: Forbidden"),
            {"url": "https://youtube-stream.example.invalid/audio", "http_headers": {}},
        ]
        source, fallback_title = music_audio_source(
            "https://www.youtube.com/watch?v=abc", "Ed Sheeran - Shape of You"
        )
        self.assertIs(source, ffmpeg.return_value)
        self.assertIsNone(fallback_title)
        self.assertEqual(youtube_dl.call_count, 2)
        options = youtube_dl.call_args_list[1].args[0]
        self.assertEqual(options["extractor_args"]["youtube"]["player_client"], ["web_embedded"])
        self.assertEqual(ffmpeg.call_args.args, ("https://youtube-stream.example.invalid/audio",))

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_prefers_safari_hls_on_first_youtube_playback_attempt(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "url": "https://example.invalid/audio.m3u8", "http_headers": {}
        }
        music_audio_source("https://www.youtube.com/watch?v=abc")
        options = youtube_dl.call_args.args[0]
        self.assertEqual(options["extractor_args"]["youtube"]["player_client"], ["web_safari"])
        self.assertEqual(options["format"], "bestaudio[protocol*=m3u8]/bestaudio/best")

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_prefers_mweb_only_when_local_pot_provider_is_ready(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "url": "https://example.invalid/audio", "http_headers": {}
        }
        with patch.dict(os.environ, {"YOUTUBE_POT_READY": "1"}):
            music_audio_source("https://www.youtube.com/watch?v=abc")
            options = youtube_dl.call_args.args[0]
            self.assertEqual(options["extractor_args"]["youtube"]["player_client"], ["mweb"])

        music_audio_source("https://www.youtube.com/watch?v=abc")
        self.assertEqual(
            youtube_dl.call_args.args[0]["extractor_args"]["youtube"]["player_client"],
            ["web_safari"],
        )

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_tries_youtube_clients_separately_when_one_is_blocked(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = [
            DownloadError("HTTP Error 403: Forbidden"),
            DownloadError("Requested format is not available"),
            {"url": "https://youtube-stream.example.invalid/audio"},
        ]
        source, fallback = music_audio_source("https://www.youtube.com/watch?v=abc", "Artist - Song")
        self.assertIs(source, ffmpeg.return_value)
        self.assertIsNone(fallback)
        self.assertEqual(youtube_dl.call_count, 3)
        self.assertEqual(youtube_dl.call_args_list[2].args[0]["extractor_args"]["youtube"]["player_client"], ["android_vr"])

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
        self.assertEqual(youtube_dl.call_count, 3)
        self.assertGreater(player_module.youtube_challenge_cooldown_seconds(), 500)

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
        self.assertEqual(youtube_dl.call_count, 2)

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_login_challenge_halts_youtube_requests_until_cooldown(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = [
            DownloadError("Sign in to confirm you're not a bot"),
            {"entries": []}, {"entries": []},
        ]
        with self.assertRaisesRegex(MusicSourceUnavailable, "ยืนยันว่าไม่ใช่บอท"):
            music_audio_source("https://www.youtube.com/watch?v=abc", "Artist Song")
        self.assertEqual(youtube_dl.call_count, 2)  # YouTube + one SoundCloud search
        with self.assertRaisesRegex(MusicSourceUnavailable, "ยืนยันว่าไม่ใช่บอท"):
            music_audio_source("https://www.youtube.com/watch?v=abc", "Artist Song")
        self.assertEqual(youtube_dl.call_count, 3)  # SoundCloud only, no more YouTube

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_discovery_reports_bot_challenge_instead_of_missing_song(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = DownloadError(
            "Sign in to confirm you're not a bot"
        )
        with self.assertRaisesRegex(MusicSourceUnavailable, "ยืนยันว่าไม่ใช่บอท"):
            discover_tracks("https://www.youtube.com/watch?v=abc")
        self.assertEqual(youtube_dl.call_count, 1)
        self.assertFalse(youtube_dl.call_args.args[0]["ignoreerrors"])

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_proxy_uses_ytdlp_for_both_discovery_and_media_bytes(self, youtube_dl, ffmpeg):
        with patch.dict(os.environ, {"YOUTUBE_PROXY_URL": "socks5://127.0.0.1:1080"}):
            youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
                "entries": [{"title": "Artist Song", "webpage_url": "https://www.youtube.com/watch?v=abc"}]
            }
            discover_tracks("Artist Song")
            self.assertEqual(youtube_dl.call_args.args[0]["proxy"], "socks5://127.0.0.1:1080")

            def write_audio(url, download):
                self.assertTrue(download)
                options = youtube_dl.call_args.args[0]
                self.assertEqual(options["proxy"], "socks5://127.0.0.1:1080")
                Path(options["outtmpl"].replace("%(ext)s", "webm")).write_bytes(b"audio")
                return {"url": url}

            ctx = youtube_dl.return_value.__enter__.return_value
            ctx.extract_info.side_effect = write_audio
            ctx.prepare_filename.side_effect = lambda info: youtube_dl.call_args.args[0]["outtmpl"].replace("%(ext)s", "webm")
            source, fallback = music_audio_source("https://www.youtube.com/watch?v=abc", "Artist Song")
            self.assertIsNone(fallback)
            self.assertTrue(Path(ffmpeg.call_args.args[0]).exists())
            source.cleanup()

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_other_youtube_errors_are_not_silently_replaced(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = DownloadError("Video unavailable")
        with self.assertRaises(DownloadError):
            music_audio_source("https://www.youtube.com/watch?v=abc", "Song")
        self.assertEqual(youtube_dl.call_count, 1)

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_cookie_file_requires_private_permissions(self, youtube_dl, ffmpeg):
        with TemporaryDirectory() as folder:
            cookie_file = Path(folder) / "cookies.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            cookie_file.chmod(0o600)
            with patch.dict(os.environ, {"YOUTUBE_COOKIES_PATH": str(cookie_file)}):
                youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {"url": "https://audio.example.invalid/a"}
                music_audio_source("https://soundcloud.com/a/b", "title")
                self.assertEqual(youtube_dl.call_args.args[0]["cookiefile"], str(cookie_file))
                cookie_file.chmod(0o644)
                music_audio_source("https://soundcloud.com/a/b", "title")
                self.assertNotIn("cookiefile", youtube_dl.call_args.args[0])

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_local_youtube_download_cleans_temporary_media(self, youtube_dl, ffmpeg):
        def download(url, download):
            self.assertTrue(download)
            options = youtube_dl.call_args.args[0]
            Path(options["outtmpl"].replace("%(ext)s", "webm")).write_bytes(b"audio")
            return {"url": url}

        ctx = youtube_dl.return_value.__enter__.return_value
        ctx.extract_info.side_effect = download
        ctx.prepare_filename.side_effect = lambda info: youtube_dl.call_args.args[0]["outtmpl"].replace("%(ext)s", "webm")
        source = downloaded_youtube_source("https://www.youtube.com/watch?v=abc")
        path = Path(ffmpeg.call_args.args[0])
        self.assertTrue(path.exists())
        self.assertEqual(source.read(), ffmpeg.return_value.read.return_value)
        source.cleanup()
        ffmpeg.return_value.cleanup.assert_called_once()
        self.assertFalse(path.exists())

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_local_youtube_download_retries_embedded_after_403(self, youtube_dl, ffmpeg):
        def download(url, download):
            if youtube_dl.call_count == 1:
                raise DownloadError("HTTP Error 403: Forbidden")
            options = youtube_dl.call_args.args[0]
            Path(options["outtmpl"].replace("%(ext)s", "m4a")).write_bytes(b"music")
            return {"url": url}

        ctx = youtube_dl.return_value.__enter__.return_value
        ctx.extract_info.side_effect = download
        ctx.prepare_filename.side_effect = lambda _: youtube_dl.call_args.args[0]["outtmpl"].replace("%(ext)s", "m4a")
        source = downloaded_youtube_source("https://www.youtube.com/watch?v=abc")
        self.assertEqual(youtube_dl.call_count, 2)
        self.assertEqual(youtube_dl.call_args_list[0].args[0]["extractor_args"]["youtube"]["player_client"], ["web_safari"])
        self.assertEqual(youtube_dl.call_args_list[1].args[0]["extractor_args"]["youtube"]["player_client"], ["web_embedded"])
        source.cleanup()


if __name__ == "__main__":
    unittest.main()
