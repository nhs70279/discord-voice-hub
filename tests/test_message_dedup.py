"""Discord events cannot cause the same message to be read twice."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.bot import VoiceHubBot


class MessageDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_join_retries_a_transient_voice_timeout(self):
        bot = VoiceHubBot()
        guild = SimpleNamespace(id=123)
        with patch.object(bot, "_auto_join_guild", new_callable=AsyncMock) as join, \
             patch("app.bot.asyncio.sleep", new_callable=AsyncMock) as sleep:
            join.side_effect = [TimeoutError(), None]
            await bot._auto_join_with_retry(guild)
        self.assertEqual(join.await_count, 2)
        sleep.assert_awaited_once_with(5)

    async def test_auto_join_stops_after_three_failed_attempts(self):
        bot = VoiceHubBot()
        guild = SimpleNamespace(id=123)
        with patch.object(bot, "_auto_join_guild", new_callable=AsyncMock) as join, \
             patch("app.bot.asyncio.sleep", new_callable=AsyncMock) as sleep:
            join.side_effect = TimeoutError()
            await bot._auto_join_with_retry(guild)
        self.assertEqual(join.await_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [5, 20])

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
