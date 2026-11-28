"""Discord should not accept its first chat while TTS cold-start is pending."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.bot import VoiceHubBot


class BotWarmupTests(unittest.IsolatedAsyncioTestCase):
    async def test_thai_warmup_finishes_before_command_setup(self):
        bot = VoiceHubBot()
        started = asyncio.Event()
        finished = asyncio.Event()

        async def warmup():
            started.set()
            await finished.wait()

        try:
            with patch("app.bot.warmup_thai", new=warmup), \
                 patch.object(bot, "add_cog", new_callable=AsyncMock) as add_cog, \
                 patch.object(bot.tree, "sync", new_callable=AsyncMock) as sync:
                setup = asyncio.create_task(bot.setup_hook())
                await asyncio.wait_for(started.wait(), timeout=1)
                add_cog.assert_not_awaited()
                sync.assert_not_awaited()
                finished.set()
                await asyncio.wait_for(setup, timeout=1)
                add_cog.assert_awaited_once()
                sync.assert_awaited_once()
        finally:
            await bot.close()


if __name__ == "__main__":
    unittest.main()
