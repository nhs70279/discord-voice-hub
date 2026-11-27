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

    async def test_youtube_stream_without_frames_recovers_with_local_audio(self):
        local_source = FakeAudioSource([bytes([7]) * FRAME_BYTES])
        with patch.object(self.module, "music_audio_source", return_value=(FakeAudioSource([]), None)), \
             patch.object(self.module, "downloaded_youtube_source", return_value=local_source) as download:
            self.player.add_tracks([self.module.Track("Song", "https://www.youtube.com/watch?v=abc", "user")])
            for _ in range(100):
                if self.player.mixer.music is not None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNotNone(self.player.mixer.music)
            self.player.mixer.read()  # Remote stream immediately produced EOF.
            for _ in range(100):
                if self.player.mixer.music is local_source:
                    break
                await asyncio.sleep(0.01)
            self.assertIs(self.player.mixer.music, local_source)
            download.assert_called_once_with("https://www.youtube.com/watch?v=abc")
            self.assertNotEqual(self.player.mixer.read(), SILENCE)
            self.assertIsNone(self.player.last_music_error)


if __name__ == "__main__":
    unittest.main()
