import os
import tempfile
import unittest
from array import array
from pathlib import Path

from app.audio_mix import FRAME_BYTES, SILENCE, diagnostic_tone_frame, mix_pcm
from app import db
from app.music_queue import MusicQueue


class AudioMixTests(unittest.TestCase):
    def test_local_diagnostic_tone_produces_stereo_audio(self):
        frame = diagnostic_tone_frame(0)
        self.assertEqual(len(frame), FRAME_BYTES)
        self.assertNotEqual(frame, SILENCE)
        samples = array("h")
        samples.frombytes(frame)
        self.assertEqual(samples[0::2], samples[1::2])

    def test_silence_frame(self):
        self.assertEqual(len(mix_pcm(SILENCE, SILENCE, 0.5)), FRAME_BYTES)
        self.assertEqual(mix_pcm(SILENCE, SILENCE, 0.5), SILENCE)

    def test_ducking_and_clipping(self):
        music = array("h", [20000] * (FRAME_BYTES // 2)).tobytes()
        speech = array("h", [20000] * (FRAME_BYTES // 2)).tobytes()
        ducked = mix_pcm(music, speech, 0.25)
        values = array("h")
        values.frombytes(ducked)
        self.assertEqual(values[0], 25000)
        loud = array("h")
        loud.frombytes(mix_pcm(music, speech, 1.0))
        self.assertEqual(loud[0], 32767)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(Path(self.temp.name) / "test.sqlite3")
        db.initialize()

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.old_db
        self.temp.cleanup()

    def test_user_voice_and_playlist_ownership(self):
        self.assertEqual(db.get_voice_config("123"), {"voice": db.DEFAULT_VOICE, "rate": 0, "pitch": 0})
        db.set_voice("123", db.DEFAULT_VOICE)
        self.assertEqual(db.get_voice("123"), db.DEFAULT_VOICE)
        db.set_voice_config("123", db.DEFAULT_VOICE, 15, -10)
        self.assertEqual(db.get_voice_config("123"), {"voice": db.DEFAULT_VOICE, "rate": 15, "pitch": -10})
        playlist_id = db.create_playlist("123", "456", "お気に入り")
        self.assertTrue(db.add_playlist_item("123", "456", playlist_id, "夜に駆ける"))
        record = db.get_playlist("123", "456", playlist_id)
        self.assertEqual(record["items"][0]["query"], "夜に駆ける")
        self.assertIsNone(db.get_playlist("987", "456", playlist_id))
        self.assertFalse(db.delete_playlist("987", "456", playlist_id))
        self.assertTrue(db.delete_playlist("123", "456", playlist_id))
        self.assertIsNone(db.get_playlist("123", "456", playlist_id))

    def test_channel_settings(self):
        self.assertIsNone(db.get_read_channel("111"))
        db.set_read_channel("111", 222)
        self.assertEqual(db.get_read_channel("111"), 222)

    def test_auto_join_defaults_on_and_is_per_guild(self):
        self.assertTrue(db.get_auto_join("111"))
        db.set_auto_join("111", False)
        self.assertFalse(db.get_auto_join("111"))
        self.assertTrue(db.get_auto_join("222"))
        db.set_read_channel("111", 333)  # Old setting remains compatible.
        self.assertFalse(db.get_auto_join("111"))

    def test_auto_join_migrates_existing_guild_settings(self):
        with db.connect() as conn:
            conn.execute("DROP TABLE guild_settings")
            conn.execute("CREATE TABLE guild_settings(guild_id TEXT PRIMARY KEY, read_channel_id TEXT)")
            conn.execute("INSERT INTO guild_settings VALUES (?,?)", ("111", "222"))
        db.initialize()
        self.assertTrue(db.get_auto_join("111"))
        self.assertEqual(db.get_read_channel("111"), 222)
        db.set_auto_join("111", False)
        self.assertFalse(db.get_auto_join("111"))

    def test_non_thai_voices_are_rejected(self):
        self.assertEqual(list(db.VOICE_CHOICES), [db.DEFAULT_VOICE])
        with self.assertRaises(ValueError):
            db.set_voice("12", "ja-JP-NanamiNeural")
        with self.assertRaises(ValueError):
            db.set_voice("12", "th-TH-NiwatNeural")
        with self.assertRaises(ValueError):
            db.set_voice_config("12", db.DEFAULT_VOICE, 50, 0)

    def test_migrates_original_user_voice_to_thai(self):
        # Simulate an existing user preference from the previous release.
        with db.connect() as conn:
            conn.execute("DROP TABLE preferences")
            conn.execute("CREATE TABLE preferences (user_id TEXT PRIMARY KEY, voice TEXT NOT NULL)")
            conn.execute("INSERT INTO preferences VALUES ('999','ja-JP-NanamiNeural')")
        db.initialize()
        self.assertEqual(db.get_voice_config("999"), {"voice": db.DEFAULT_VOICE, "rate": 0, "pitch": 0})

    def test_migrates_previous_male_voice_without_losing_preferences(self):
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO preferences(user_id,voice,rate,pitch) VALUES (?,?,?,?)",
                ("888", "th-TH-NiwatNeural", -5, 10),
            )
        db.initialize()
        self.assertEqual(db.get_voice_config("888"), {"voice": db.DEFAULT_VOICE, "rate": -5, "pitch": 10})


class MusicQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_preserves_playlist_order(self):
        queue = MusicQueue[str]()
        queue.add_many(["A", "B"])
        queue.add_many(["C", "D"], front=True)
        self.assertEqual(queue.snapshot(), ["C", "D", "A", "B"])
        self.assertEqual(await queue.get(), "C")
        self.assertEqual(queue.snapshot(), ["D", "A", "B"])

    async def test_remove_and_move_by_display_index(self):
        queue = MusicQueue[str]()
        queue.add_many(["A", "B", "C", "D"])
        self.assertEqual(queue.remove(2), "B")
        self.assertEqual(queue.snapshot(), ["A", "C", "D"])
        queue.move(3, 1)
        self.assertEqual(queue.snapshot(), ["D", "A", "C"])
        with self.assertRaises(ValueError):
            queue.remove(7)

    async def test_clear_and_capacity(self):
        queue = MusicQueue[str](max_size=2)
        queue.add_many(["A", "B"])
        with self.assertRaises(ValueError):
            queue.add_many(["C"])
        self.assertEqual(queue.snapshot(), ["A", "B"])
        queue.clear()
        queue.add_many(["D"])
        self.assertEqual(await queue.get(), "D")

    async def test_wait_for_new_song(self):
        queue = MusicQueue[str]()
        import asyncio
        waiting = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        queue.add_many(["new song"])
        self.assertEqual(await asyncio.wait_for(waiting, timeout=1), "new song")


if __name__ == "__main__":
    unittest.main()
