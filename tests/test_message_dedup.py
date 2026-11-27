"""Discord events cannot cause the same message to be read twice."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.bot import VoiceHubBot


class MessageDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_discord_message_is_only_spoken_once(self):
        bot = VoiceHubBot()
        player = SimpleNamespace(
            vc=SimpleNamespace(is_connected=lambda: True, channel=object()),
            chat_events_seen=0,
            chat_events_without_content=0,
            chat_events_in_read_channel=0,
            chat_messages_private_skipped=0,
            chat_messages_enqueued=0,
            add_speech=AsyncMock(return_value=True),
        )
        bot.guild_players[123] = player
        message = SimpleNamespace(
            id=98765,
            guild=SimpleNamespace(id=123),
            author=SimpleNamespace(bot=False, id=25, display_name="คนทดสอบ"),
            channel=object(),
            content="สวัสดี",
        )
        with patch("app.bot.db.get_auto_join", return_value=False), \
             patch("app.bot.visible_to_voice_listeners", return_value=True), \
             patch("app.bot.db.get_voice_config", return_value={}):
            await bot.on_message(message)
            await bot.on_message(message)
        player.add_speech.assert_awaited_once()
        self.assertEqual(player.chat_messages_enqueued, 1)
