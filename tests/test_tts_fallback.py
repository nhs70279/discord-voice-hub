"""TTS provider failover never needs live Microsoft or Google credentials."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import tts_fallback


class ThaiTTSTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tts_fallback._edge_retry_at = 0.0

    def tearDown(self):
        tts_fallback._edge_retry_at = 0.0

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

    async def test_no_audio_received_falls_back_without_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts.mp3"

            def fake_backup(text, target):
                target.write_bytes(b"G" * 300)

            with patch.object(tts_fallback, "_edge_save", new=AsyncMock(side_effect=RuntimeError("NoAudioReceived"))) as edge:
                with patch.object(tts_fallback, "_google_save", side_effect=fake_backup) as google:
                    result = await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
            self.assertEqual(result, "Google Thai (backup)")
            self.assertEqual(edge.await_count, 1)
            self.assertEqual(google.call_count, 1)
            self.assertGreater(path.stat().st_size, 256)

    async def test_failing_edge_is_skipped_until_cooldown_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts.mp3"

            def fake_backup(text, target):
                target.write_bytes(b"G" * 300)

            with patch.object(tts_fallback, "_edge_save", new=AsyncMock(side_effect=RuntimeError("NoAudioReceived"))) as edge, \
                 patch.object(tts_fallback, "_google_save", side_effect=fake_backup) as google:
                await tts_fallback.synthesize_thai("หนึ่ง", {"voice": "thai"}, path)
                await tts_fallback.synthesize_thai("สอง", {"voice": "thai"}, path)
                self.assertEqual(edge.await_count, 1)
                self.assertEqual(google.call_count, 2)
                tts_fallback._edge_retry_at = 0.0
                await tts_fallback.synthesize_thai("สาม", {"voice": "thai"}, path)
                self.assertEqual(edge.await_count, 2)

    async def test_both_synthesis_providers_fail_with_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts.mp3"
            with patch.object(tts_fallback, "_edge_save", new=AsyncMock(side_effect=RuntimeError("NoAudioReceived"))):
                with patch.object(tts_fallback, "_google_save", side_effect=ConnectionError("offline")):
                    with self.assertRaisesRegex(RuntimeError, "Edge RuntimeError; backup ConnectionError"):
                        await tts_fallback.synthesize_thai("สวัสดี", {"voice": "thai"}, path)
