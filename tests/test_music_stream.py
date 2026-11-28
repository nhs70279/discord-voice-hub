"""Music resolution and playback must use YouTube alone."""

import os
import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import app.player as player_module
    from app.player import (
        MusicSourceUnavailable, discover_tracks, downloaded_youtube_source,
        music_audio_source, valid_query,
    )
    from yt_dlp.utils import DownloadError
except ImportError:  # Optional Discord voice dependencies are absent.
    music_audio_source = None


@unittest.skipIf(music_audio_source is None, "Discord voice dependencies are unavailable")
class MusicStreamTests(unittest.TestCase):
    YOUTUBE_URL = "https://www.youtube.com/watch?v=abc"

    def setUp(self):
        old_until = player_module._youtube_challenge_until
        player_module._youtube_challenge_until = 0.0
        self.addCleanup(setattr, player_module, "_youtube_challenge_until", old_until)
        env_patch = patch.dict(os.environ, {
            "YOUTUBE_PROXY_URL": "", "YOUTUBE_POT_READY": "", "YOUTUBE_COOKIES_PATH": "",
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_youtube_links_and_keywords_only(self):
        for query in ["เพลงไทย", self.YOUTUBE_URL, "https://youtu.be/abc", "https://music.youtube.com/watch?v=abc"]:
            self.assertTrue(valid_query(query))
        for query in [
            "https://soundcloud.com/singer/song", "https://on.soundcloud.com/shortlink",
            "http://www.youtube.com/watch?v=abc", "https://youtube.com.attacker.invalid/watch?v=abc",
            "https://example.invalid/song", "",
        ]:
            self.assertFalse(valid_query(query), query)

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_keyword_search_uses_one_youtube_request(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [{"title": "A song", "webpage_url": self.YOUTUBE_URL}],
        }
        self.assertEqual(discover_tracks("A song"), [{"title": "A song", "url": self.YOUTUBE_URL}])
        self.assertEqual(youtube_dl.call_count, 1)
        self.assertEqual(youtube_dl.return_value.__enter__.return_value.extract_info.call_args.args, ("A song",))
        self.assertEqual(youtube_dl.call_args.args[0]["default_search"], "ytsearch1")

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_direct_video_avoids_duplicate_youtube_extraction(self, youtube_dl):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        tracks = discover_tracks(url)
        self.assertEqual(tracks, [{"title": player_module.YOUTUBE_TITLE_PENDING, "url": url}])
        self.assertEqual(discover_tracks("https://youtu.be/dQw4w9WgXcQ"), [
            {"title": player_module.YOUTUBE_TITLE_PENDING, "url": "https://youtu.be/dQw4w9WgXcQ"},
        ])
        youtube_dl.assert_not_called()

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_playlist_link_keeps_youtube_expansion(self, youtube_dl):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL-example"
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [{"title": "Playlist item", "webpage_url": self.YOUTUBE_URL}],
        }
        self.assertEqual(discover_tracks(url), [{"title": "Playlist item", "url": self.YOUTUBE_URL}])
        youtube_dl.assert_called_once()

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_no_fallback_when_youtube_returns_no_results(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {"entries": []}
        self.assertEqual(discover_tracks("obscure track"), [])
        self.assertEqual(youtube_dl.call_count, 1)

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_other_platform_rejected_before_extraction(self, youtube_dl):
        with self.assertRaises(ValueError):
            discover_tracks("https://soundcloud.com/artist/track")
        with self.assertRaises(ValueError):
            music_audio_source("https://soundcloud.com/artist/track")
        youtube_dl.assert_not_called()

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_http_headers_are_forwarded_safely(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "url": "https://example.invalid/audio",
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (example)",
                "Referer": "https://www.youtube.com/",
                "X-Unsafe": "do-not-forward",
                "Origin": "https://www.youtube.com/\r\nInjected: true",
            },
        }
        source = music_audio_source(self.YOUTUBE_URL)
        self.assertIs(source, ffmpeg.return_value)
        args = shlex.split(ffmpeg.call_args.kwargs["before_options"])
        headers = args[args.index("-headers") + 1]
        self.assertIn("User-Agent: Mozilla/5.0 (example)\r\n", headers)
        self.assertIn("Referer: https://www.youtube.com/\r\n", headers)
        self.assertNotIn("X-Unsafe", headers)
        self.assertNotIn("Injected", headers)
        self.assertEqual(ffmpeg.call_args.args, ("https://example.invalid/audio",))

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_playback_updates_title_for_fast_direct_video(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "url": "https://example.invalid/audio", "title": "Resolved YouTube title",
        }
        names = []
        self.assertIs(music_audio_source(self.YOUTUBE_URL, title_callback=names.append), ffmpeg.return_value)
        self.assertEqual(names, ["Resolved YouTube title"])

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_blocked_youtube_uses_cooldown_without_other_sources(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = DownloadError(
            "Sign in to confirm you're not a bot"
        )
        with self.assertRaisesRegex(MusicSourceUnavailable, "ยืนยันว่าไม่ใช่บอท"):
            music_audio_source(self.YOUTUBE_URL)
        with self.assertRaisesRegex(MusicSourceUnavailable, "ยืนยันว่าไม่ใช่บอท"):
            music_audio_source(self.YOUTUBE_URL)
        self.assertEqual(youtube_dl.call_count, 1)

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_discovery_challenge_does_not_search_elsewhere(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = DownloadError(
            "Sign in to confirm you're not a bot"
        )
        with self.assertRaisesRegex(MusicSourceUnavailable, "ยืนยันว่าไม่ใช่บอท"):
            discover_tracks("song title")
        self.assertEqual(youtube_dl.call_count, 1)

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_other_errors_do_not_trigger_secondary_requests(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = DownloadError("Video unavailable")
        with self.assertRaises(DownloadError):
            music_audio_source(self.YOUTUBE_URL)
        self.assertEqual(youtube_dl.call_count, 1)

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_provider_uses_multiple_youtube_clients_in_one_extraction(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {"url": "https://audio.example.invalid/a"}
        with patch.dict(os.environ, {"YOUTUBE_POT_READY": "1"}):
            music_audio_source(self.YOUTUBE_URL)
        self.assertEqual(youtube_dl.call_args.args[0]["extractor_args"]["youtube"]["player_client"], ["mweb", "tv", "web_safari"])
        self.assertEqual(youtube_dl.call_count, 1)
        with patch.dict(os.environ, {"YOUTUBE_POT_READY": "0"}):
            music_audio_source(self.YOUTUBE_URL)
        self.assertEqual(youtube_dl.call_args.args[0]["extractor_args"]["youtube"]["player_client"], ["web_safari"])

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_proxy_downloads_youtube_through_same_egress(self, youtube_dl, ffmpeg):
        with patch.dict(os.environ, {"YOUTUBE_PROXY_URL": "socks5://127.0.0.1:1080"}):
            def save_audio(url, download):
                self.assertTrue(download)
                options = youtube_dl.call_args.args[0]
                self.assertEqual(options["proxy"], "socks5://127.0.0.1:1080")
                Path(options["outtmpl"].replace("%(ext)s", "webm")).write_bytes(b"audio")
                return {"url": url}

            ctx = youtube_dl.return_value.__enter__.return_value
            ctx.extract_info.side_effect = save_audio
            ctx.prepare_filename.side_effect = lambda _: youtube_dl.call_args.args[0]["outtmpl"].replace("%(ext)s", "webm")
            source = music_audio_source(self.YOUTUBE_URL)
            self.assertTrue(Path(ffmpeg.call_args.args[0]).exists())
            self.assertEqual(youtube_dl.call_count, 1)
            source.cleanup()

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_cookie_permissions_are_private(self, youtube_dl, ffmpeg):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {"url": "https://audio.example.invalid/a"}
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            path.chmod(0o600)
            with patch.dict(os.environ, {"YOUTUBE_COOKIES_PATH": str(path)}):
                music_audio_source(self.YOUTUBE_URL)
                self.assertEqual(youtube_dl.call_args.args[0]["cookiefile"], str(path))
                path.chmod(0o644)
                music_audio_source(self.YOUTUBE_URL)
                self.assertNotIn("cookiefile", youtube_dl.call_args.args[0])

    @patch("app.player.discord.FFmpegPCMAudio")
    @patch("app.player.yt_dlp.YoutubeDL")
    def test_youtube_download_cleans_temporary_media(self, youtube_dl, ffmpeg):
        def download(url, download):
            self.assertTrue(download)
            options = youtube_dl.call_args.args[0]
            Path(options["outtmpl"].replace("%(ext)s", "webm")).write_bytes(b"audio")
            return {"url": url}

        ctx = youtube_dl.return_value.__enter__.return_value
        ctx.extract_info.side_effect = download
        ctx.prepare_filename.side_effect = lambda _: youtube_dl.call_args.args[0]["outtmpl"].replace("%(ext)s", "webm")
        source = downloaded_youtube_source(self.YOUTUBE_URL)
        path = Path(ffmpeg.call_args.args[0])
        self.assertTrue(path.exists())
        self.assertEqual(source.read(), ffmpeg.return_value.read.return_value)
        source.cleanup()
        ffmpeg.return_value.cleanup.assert_called_once()
        self.assertFalse(path.exists())

    @patch("app.player.yt_dlp.YoutubeDL")
    def test_failed_youtube_download_does_not_retry_media(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.side_effect = DownloadError("HTTP Error 403")
        with self.assertRaises(DownloadError):
            downloaded_youtube_source(self.YOUTUBE_URL)
        self.assertEqual(youtube_dl.call_count, 1)


if __name__ == "__main__":
    unittest.main()
