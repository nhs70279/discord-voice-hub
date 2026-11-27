"""Auto-join channel selection without a live Discord connection."""

import unittest
from types import SimpleNamespace

from app.voice_policy import human_count, occupied_channel, visible_to_voice_listeners


def channel(humans, bots=0):
    return SimpleNamespace(members=[SimpleNamespace(bot=False) for _ in range(humans)] +
                           [SimpleNamespace(bot=True) for _ in range(bots)])


class VoicePolicyTests(unittest.TestCase):
    def test_humans_not_bots_keep_channel_occupied(self):
        self.assertEqual(human_count(channel(0, bots=2)), 0)
        self.assertEqual(human_count(channel(3, bots=2)), 3)

    def test_auto_join_busiest_room_when_current_is_empty(self):
        empty = channel(0, bots=1)
        quiet = channel(1)
        busy = channel(3)
        guild = SimpleNamespace(voice_channels=[empty, quiet, busy])
        self.assertIs(occupied_channel(guild, empty), busy)

    def test_stays_in_room_with_listeners_until_a_chat_redirect(self):
        current = channel(1)
        busier = channel(4)
        guild = SimpleNamespace(voice_channels=[current, busier])
        self.assertIs(occupied_channel(guild, current), current)

    def test_no_humans_means_no_auto_join(self):
        guild = SimpleNamespace(voice_channels=[channel(0), channel(0, bots=1)])
        self.assertIsNone(occupied_channel(guild))

    def test_private_messages_are_not_broadcast_to_unauthorized_voice_listeners(self):
        first = SimpleNamespace(bot=False, id=1)
        second = SimpleNamespace(bot=False, id=2)
        voice = SimpleNamespace(members=[first, second])
        text = SimpleNamespace(permissions_for=lambda member: SimpleNamespace(view_channel=member.id == 1))
        self.assertFalse(visible_to_voice_listeners(text, voice))
        text.permissions_for = lambda member: SimpleNamespace(view_channel=True)
        self.assertTrue(visible_to_voice_listeners(text, voice))
