"""Exercise the low-CPU voice sender without Discord/network dependencies."""

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.audio_mix import FRAME_BYTES, SILENCE


class FakeAudioSource:
    def __init__(self, frames=()):
        self.frames = iter(frames)
        self.cleaned = False

    def read(self):
        return next(self.frames, b"")

    def cleanup(self):
        self.cleaned = True


class FakeVoiceClient:
    def __init__(self):
        self.guild = types.SimpleNamespace(id=123)
        self.status = "stopped"
        self.pause_count = 0
        self.resume_count = 0

    def play(self, source, *, after):
        self.source = source
        self.after = after
        self.status = "playing"

    def pause(self):
        self.pause_count += 1
        self.status = "paused"

    def resume(self):
        self.resume_count += 1
        self.status = "playing"

    def is_playing(self):
        return self.status == "playing"

    def is_paused(self):
        return self.status == "paused"

    def is_connected(self):
        return self.status != "stopped"

    def stop(self):
        self.status = "stopped"

    async def disconnect(self, *, force):
        self.status = "stopped"


def load_player_with_fake_discord():
    """Import a separate player module while stubbing only external voice APIs."""
    fake_discord = types.ModuleType("discord")
    fake_discord.AudioSource = FakeAudioSource
    fake_discord.VoiceClient = FakeVoiceClient
    fake_discord.FFmpegPCMAudio = FakeAudioSource
    fake_ytdlp = types.ModuleType("yt_dlp")
    fake_ytdlp.YoutubeDL = object
    name = "app._player_idle_test"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "app" / "player.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"discord": fake_discord, "yt_dlp": fake_ytdlp, name: module}):
        spec.loader.exec_module(module)
    sys.modules.pop(name, None)
    return module


class IdleVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = load_player_with_fake_discord()
        self.voice = FakeVoiceClient()
        self.player = self.module.GuildPlayer(self.voice, asyncio.get_running_loop())

    async def asyncTearDown(self):
        await self.player.shutdown()

    async def test_new_connection_is_paused_instead_of_encoding_silence(self):
        self.assertTrue(self.voice.is_paused())
        self.assertEqual(self.voice.pause_count, 1)
        self.assertFalse(self.player.mixer.has_active_audio())

    async def test_test_tone_wakes_sender_then_parks_when_finished(self):
        frame = bytes([1]) * FRAME_BYTES
        with patch.object(self.module, "DiagnosticTone", return_value=FakeAudioSource([frame])):
            self.player.play_test_tone()
        self.assertTrue(self.voice.is_playing())
        self.assertEqual(len(self.player.mixer.read()), FRAME_BYTES)
        self.assertEqual(self.player.mixer.read(), SILENCE)
        await asyncio.sleep(0)
        self.assertTrue(self.voice.is_paused())
        self.assertEqual(self.voice.pause_count, 2)

    async def test_music_pauses_without_blocking_subsequent_speech(self):
        music = FakeAudioSource([bytes([2]) * FRAME_BYTES])
        self.player.mixer.set_music(music)
        self.player._resume_audio()
        self.assertTrue(self.voice.is_playing())
        self.player.pause_music()
        self.assertTrue(self.voice.is_paused())
        speech = FakeAudioSource([bytes([3]) * FRAME_BYTES])
        self.player.mixer.enqueue_speech(speech, None)
        self.player._resume_audio()
        self.assertTrue(self.voice.is_playing())
        self.assertNotEqual(self.player.mixer.read(), SILENCE)
        self.assertEqual(self.player.mixer.read(), SILENCE)
        await asyncio.sleep(0)
        self.assertTrue(self.voice.is_paused())
        self.player.resume_music()
        self.assertTrue(self.voice.is_playing())
        self.assertNotEqual(self.player.mixer.read(), SILENCE)
        self.assertEqual(self.player.mixer.read(), SILENCE)
        await asyncio.sleep(0)
        self.assertTrue(self.voice.is_paused())

    async def test_stale_idle_notification_does_not_pause_new_audio(self):
        self.player.mixer.read()  # Schedule an idle callback from the audio thread.
        self.player.mixer.enqueue_speech(FakeAudioSource([bytes([1]) * FRAME_BYTES]), None)
        self.player._resume_audio()
        await asyncio.sleep(0)
        self.assertTrue(self.voice.is_playing())

    async def test_tts_start_is_measured_on_first_pcm_frame_only(self):
        frame = bytes([3]) * FRAME_BYTES
        timestamps = []
        self.player.mixer.enqueue_speech(FakeAudioSource([frame, frame]), None, on_start=timestamps.append)
        self.player._resume_audio()
        self.player.mixer.read()
        self.player.mixer.read()
        await asyncio.sleep(0)
        self.assertEqual(len(timestamps), 1)
        self.assertIsInstance(timestamps[0], float)

    async def test_tts_latency_breakdown_is_recorded(self):
        self.player._record_tts_latency(1.0, 1.1, 1.3, 2.1, 2.6, 80)
        self.assertEqual(self.player.last_tts_latency_ms, {
            "discord": 80, "queue": 200, "synthesis": 800,
            "playback_wait": 500, "total": 1680,
        })
        self.assertEqual(list(self.player.tts_latency_samples), [1680])

    async def test_tts_queue_drops_oldest_messages_to_keep_chat_current(self):
        # Don't let the worker consume messages during this queue-capacity test.
        self.player.tts_task.cancel()
        await asyncio.gather(self.player.tts_task, return_exceptions=True)
        for number in range(10):
            self.assertTrue(await self.player.add_speech(str(number), {}))
        self.assertEqual(self.player.tts_queue.qsize(), 8)
        self.assertEqual(self.player.chat_messages_dropped, 2)
        queued = [self.player.tts_queue.get_nowait() for _ in range(8)]
        self.assertEqual(len(queued), 8)
        for request in queued:
            self.player._discard_preparation(request.preparation)
            self.player.tts_queue.task_done()

    async def test_tts_worker_skips_old_messages_before_synthesizing(self):
        from unittest.mock import AsyncMock

        with patch.object(self.module, "synthesize_thai", new=AsyncMock()) as synthesize:
            old = self.player.loop.time() - 40
            unused = self.player.loop.create_future()
            self.player.tts_queue.put_nowait(self.module.SpeechRequest(unused, old, old, None))
            await asyncio.wait_for(self.player.tts_queue.join(), timeout=1)
            synthesize.assert_not_awaited()
        self.assertEqual(self.player.chat_messages_dropped, 1)

    async def test_two_tts_requests_prepare_concurrently_but_play_in_order(self):
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        finish_first = asyncio.Event()

        async def fake_synthesis(message, _settings, path):
            if message == "first":
                first_started.set()
                await finish_first.wait()
            else:
                second_started.set()
            path.write_bytes(message.encode() * 100)
            return "Google Thai"

        with patch.object(self.module, "synthesize_thai", side_effect=fake_synthesis), \
             patch.object(self.module.discord, "FFmpegPCMAudio", side_effect=lambda path, **_: FakeAudioSource([Path(path).read_bytes()[:FRAME_BYTES]])):
            await self.player.add_speech("first", {})
            await self.player.add_speech("second", {})
            await asyncio.wait_for(first_started.wait(), timeout=1)
            await asyncio.wait_for(second_started.wait(), timeout=1)
            self.assertFalse(finish_first.is_set())
            finish_first.set()
            await asyncio.wait_for(self.player.tts_queue.join(), timeout=1)

        self.assertEqual(len(self.player.mixer.speech), 2)
        queued_sources = [entry[0] for entry in self.player.mixer.speech]
        self.assertEqual(next(queued_sources[0].frames)[:5], b"first")
        self.assertEqual(next(queued_sources[1].frames)[:6], b"second")

    async def test_dropping_prefetched_speech_removes_temp_file(self):
        async def fake_synthesis(_message, _settings, path):
            path.write_bytes(b"F" * 300)
            return "Google Thai"

        with patch.object(self.module, "synthesize_thai", side_effect=fake_synthesis):
            self.player.tts_task.cancel()
            await asyncio.gather(self.player.tts_task, return_exceptions=True)
            await self.player.add_speech("discard me", {})
            request = self.player.tts_queue.get_nowait()
            prepared = await asyncio.wait_for(request.preparation, timeout=1)
            self.assertTrue(prepared.path.exists())
            self.player._discard_preparation(request.preparation)
            self.assertFalse(prepared.path.exists())
            self.player.tts_queue.task_done()

    async def test_cancelling_inflight_download_cleans_file_after_network_finishes(self):
        started = asyncio.Event()
        finish = asyncio.Event()
        created_path = []

        async def fake_synthesis(_message, _settings, path):
            created_path.append(path)
            started.set()
            await finish.wait()
            path.write_bytes(b"F" * 300)
            return "Google Thai"

        with patch.object(self.module, "synthesize_thai", side_effect=fake_synthesis):
            self.player.tts_task.cancel()
            await asyncio.gather(self.player.tts_task, return_exceptions=True)
            await self.player.add_speech("discard after download", {})
            request = self.player.tts_queue.get_nowait()
            await asyncio.wait_for(started.wait(), timeout=1)
            self.player._discard_preparation(request.preparation)
            finish.set()
            await asyncio.gather(request.preparation, return_exceptions=True)
            self.player.tts_queue.task_done()

        self.assertTrue(request.preparation.cancelled())
        self.assertFalse(created_path[0].exists())

    async def test_youtube_empty_stream_reports_error_without_extra_media_requests(self):
        source = FakeAudioSource([])
        with patch.object(self.module, "music_audio_source", return_value=source) as resolve, \
             patch.object(self.module, "downloaded_youtube_source") as download:
            self.player.add_tracks([self.module.Track("Song", "https://www.youtube.com/watch?v=abc", "user")])
            for _ in range(100):
                if self.player.mixer.music is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertIs(self.player.mixer.music, source)
            self.player.mixer.read()  # The YouTube stream produces no PCM.
            for _ in range(100):
                if self.player.current is None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNone(self.player.current)
            self.assertEqual(self.player.playback_status, "error")
            self.assertIn("สตรีมเสียง", self.player.last_music_error)
            resolve.assert_called_once()
            self.assertEqual(resolve.call_args.args, ("https://www.youtube.com/watch?v=abc",))
            self.assertTrue(callable(resolve.call_args.kwargs["timing_callback"]))
            download.assert_not_called()

    async def test_fast_youtube_url_updates_title_after_stream_resolution(self):
        track = self.module.Track(
            self.module.YOUTUBE_TITLE_PENDING,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "user",
        )
        stream = FakeAudioSource([bytes([3]) * FRAME_BYTES])

        def resolve(_url, *, title_callback, timing_callback):
            title_callback("Actual YouTube song")
            timing_callback({"เริ่มต้น": 110, "mweb": 740})
            return stream

        with patch.object(self.module, "music_audio_source", side_effect=resolve):
            self.player.add_tracks([track])
            for _ in range(100):
                if self.player.mixer.music is stream:
                    break
                await asyncio.sleep(0.01)
        self.assertIs(self.player.mixer.music, stream)
        self.assertEqual(track.title, "Actual YouTube song")
        self.assertEqual(self.player.last_music_stages, {"เริ่มต้น": 110, "mweb": 740})


if __name__ == "__main__":
    unittest.main()
