"""TTS provider failover never needs live Microsoft or Google credentials."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import tts_fallback


class ThaiTTSTests(unittest.IsolatedAsyncioTestCase):
    async def test_neural_voice_succeeds_without_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts.mp3"

            async def fake_neural(text, settings, target):
                target.write_bytes(b"A" * 300)

            with patch.object(tts_fallback, "_edge_save", side_effect=fake_neural) as edge:
                with patch.object(tts_fallback, "_google_save") as google:
                    result = await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
            self.assertEqual(result, "Edge Neural")
            self.assertEqual(edge.call_count, 1)
            google.assert_not_called()

    async def test_no_audio_received_retries_then_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts.mp3"

            def fake_backup(text, target):
                target.write_bytes(b"G" * 300)

            with patch.object(tts_fallback, "_edge_save", new=AsyncMock(side_effect=RuntimeError("NoAudioReceived"))) as edge:
                with patch.object(tts_fallback, "_google_save", side_effect=fake_backup) as google:
                    with patch.object(tts_fallback.asyncio, "sleep", new=AsyncMock()):
                        result = await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
            self.assertEqual(result, "Google Thai (backup)")
            self.assertEqual(edge.await_count, 3)
            self.assertEqual(google.call_count, 1)
            self.assertGreater(path.stat().st_size, 256)

    async def test_both_synthesis_providers_fail_with_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts.mp3"
            with patch.object(tts_fallback, "_edge_save", new=AsyncMock(side_effect=RuntimeError("NoAudioReceived"))):
                with patch.object(tts_fallback, "_google_save", side_effect=ConnectionError("offline")):
                    with patch.object(tts_fallback.asyncio, "sleep", new=AsyncMock()):
                        with self.assertRaisesRegex(RuntimeError, "Edge RuntimeError; backup ConnectionError"):
                            await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
