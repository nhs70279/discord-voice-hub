"""Thai-only spoken chat text should not include Japanese prefixes or URL noise."""

import unittest

from app.tts_text import speech_text


class SpeechTextTests(unittest.TestCase):
    def test_spoken_text_uses_thai_attribution(self):
        self.assertEqual(speech_text("มะลิ", "สวัสดีค่ะ"), "มะลิ พิมพ์ว่า สวัสดีค่ะ")

    def test_link_and_mentions_are_cleaned(self):
        actual = speech_text("เกม", "ดูนี่ https://example.com/a?x=1 <@1234> **น่ารัก**")
        self.assertEqual(actual, "เกม พิมพ์ว่า ดูนี่ ลิงก์ น่ารัก")

    def test_discord_emotes_and_code_blocks_are_omitted(self):
        self.assertEqual(speech_text("ผู้ใช้", "<:abc:1234> ```print('hello')``` ดีจ้า"), "ผู้ใช้ พิมพ์ว่า ดีจ้า")

    def test_empty_or_mention_only_is_silent(self):
        self.assertEqual(speech_text("user", "<@1234>"), "")
        self.assertEqual(speech_text("user", ""), "")


if __name__ == "__main__":
    unittest.main()
