"""Thai TTS keeps one provider and one voice for a whole bot run."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import tts_fallback


class ThaiTTSTests(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_primes_one_thai_audio_clip_without_switching_engine(self):
        with patch.dict(os.environ, {"LILIN_TTS_WARMUP": "1"}):
            async def fake_synthesis(text, settings, path):
                self.assertEqual(text, "สวัสดีค่ะ")
                path.write_bytes(b"G" * 300)
                return "Google Thai"

            with patch.object(tts_fallback, "synthesize_thai", side_effect=fake_synthesis) as synth:
                await tts_fallback.warmup_thai()
        self.assertEqual(synth.call_count, 1)

    async def test_warmup_can_be_disabled(self):
        with patch.dict(os.environ, {"LILIN_TTS_WARMUP": "0"}):
            with patch.object(tts_fallback, "synthesize_thai") as synth:
                await tts_fallback.warmup_thai()
        synth.assert_not_called()

    async def test_failed_warmup_does_not_block_subsequent_tts(self):
        with patch.dict(os.environ, {"LILIN_TTS_WARMUP": "1"}):
            with patch.object(tts_fallback, "synthesize_thai", new=AsyncMock(side_effect=RuntimeError("test"))) as synth:
                await tts_fallback.warmup_thai()
        synth.assert_awaited_once()

    async def test_google_voice_is_the_only_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LILIN_TTS_ENGINE": "google"}):
            path = Path(tmp) / "tts.mp3"

            def fake_google(text, target):
                target.write_bytes(b"G" * 300)

            with patch.object(tts_fallback, "_google_save", side_effect=fake_google) as google:
                with patch.object(tts_fallback, "_edge_save") as edge:
                    result = await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
            self.assertEqual(result, "Google Thai")
            self.assertEqual(google.call_count, 1)
            edge.assert_not_called()

    async def test_neural_voice_can_be_explicitly_selected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LILIN_TTS_ENGINE": "edge"}):
            path = Path(tmp) / "tts.mp3"

            async def fake_neural(text, settings, target):
                target.write_bytes(b"E" * 300)

            with patch.object(tts_fallback, "_edge_save", side_effect=fake_neural) as edge:
                with patch.object(tts_fallback, "_google_save") as google:
                    result = await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
            self.assertEqual(result, "Edge Neural")
            self.assertEqual(edge.call_count, 1)
            google.assert_not_called()

    async def test_failed_neural_does_not_change_speaker(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LILIN_TTS_ENGINE": "edge"}):
            path = Path(tmp) / "tts.mp3"
            with patch.object(tts_fallback, "_edge_save", new=AsyncMock(side_effect=RuntimeError("NoAudioReceived"))):
                with patch.object(tts_fallback, "_google_save") as google:
                    with self.assertRaisesRegex(RuntimeError, "NoAudioReceived"):
                        await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
            google.assert_not_called()

    async def test_empty_google_audio_is_reported_without_switching(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LILIN_TTS_ENGINE": "google"}):
            path = Path(tmp) / "tts.mp3"
            with patch.object(tts_fallback, "_google_save") as google:
                with patch.object(tts_fallback, "_edge_save") as edge:
                    with self.assertRaisesRegex(RuntimeError, "produced no audio"):
                        await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
            self.assertEqual(google.call_count, 1)
            edge.assert_not_called()
