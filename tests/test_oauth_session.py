"""Discord errors and OAuth token refresh must not produce false expiry alerts."""

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

try:
    from app import server
except ModuleNotFoundError as error:
    if error.name not in {"discord", "edge_tts", "yt_dlp", "nacl"}:
        raise
    # This module tests HTTP OAuth logic, which does not need a Discord client.
    with patch.dict("sys.modules", {"discord": MagicMock(), "app.bot": MagicMock()}):
        from app import server


class OAuthSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server._guild_cache.clear()

    async def test_dashboard_reuses_guild_membership_across_parallel_requests(self):
        guild = {"id": "123", "name": "My server", "permissions": "32"}
        bot = SimpleNamespace(guilds=[SimpleNamespace(id=123)])
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)))
        api = AsyncMock(return_value=[guild])
        with patch.object(server, "discord_api", new=api):
            results = await asyncio.gather(*(
                server.user_guilds(request, session={"access_token": "token-A"})
                for _ in range(8)
            ))
            self.assertEqual(results, [[guild]] * 8)
            self.assertEqual(api.await_count, 1)
            # Live bot membership is checked on every request, even on a cache hit.
            bot.guilds = []
            self.assertEqual(await server.user_guilds(request, session={"access_token": "token-A"}), [])
            self.assertEqual(api.await_count, 1)

    async def test_dashboard_does_not_reuse_guild_cache_after_token_rotation(self):
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            bot=SimpleNamespace(guilds=[SimpleNamespace(id=123)]))))
        api = AsyncMock(return_value=[{"id": "123", "permissions": "32"}])
        with patch.object(server, "discord_api", new=api):
            await server.user_guilds(request, session={"access_token": "old-token"})
            await server.user_guilds(request, session={"access_token": "new-token"})
            self.assertEqual(api.await_count, 2)

    async def test_failed_guild_lookup_is_not_cached(self):
        api = AsyncMock(side_effect=HTTPException(503, "Discord rate limited"))
        with patch.object(server, "discord_api", new=api):
            for _ in range(2):
                with self.assertRaises(HTTPException):
                    await server.cached_discord_guilds("rate-limited-token")
            self.assertEqual(api.await_count, 2)

    async def test_discord_429_exposes_retry_after_to_dashboard(self):
        calls = 0

        def rate_limited(request):
            nonlocal calls
            calls += 1
            return httpx.Response(429, headers={"Retry-After": "45"}, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(rate_limited))
        with patch.object(server.httpx, "AsyncClient", return_value=client):
            with self.assertRaises(HTTPException) as raised:
                await server.discord_api("/users/@me/guilds", "token")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.headers["Retry-After"], "45")
        self.assertEqual(calls, 1)

    async def test_web_settings_toggle_auto_join_and_schedule_existing_listeners(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATABASE_PATH": str(Path(directory) / "test.sqlite3")}):
                server.db.initialize()
                guild = SimpleNamespace(id=123)
                bot = SimpleNamespace(get_guild=lambda ident: guild, schedule_auto_join=MagicMock())
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)))
                admin = {"permissions": str(0x28)}
                await server.update_guild_settings(request, "123", server.GuildSetting(auto_join_enabled=False), admin)
                self.assertFalse(server.db.get_auto_join("123"))
                bot.schedule_auto_join.assert_not_called()
                await server.update_guild_settings(request, "123", server.GuildSetting(auto_join_enabled=True), admin)
                self.assertTrue(server.db.get_auto_join("123"))
                bot.schedule_auto_join.assert_called_once_with(guild)

    async def test_temporary_discord_error_is_not_reported_as_expired_session(self):
        for status, expected in ((403, 403), (429, 503), (502, 503), (401, 401)):
            with self.subTest(status=status):
                response = httpx.Response(status, request=httpx.Request("GET", "https://discord.com/api/v10/users/@me/guilds"))
                client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
                with patch.object(server.httpx, "AsyncClient", return_value=client):
                    with self.assertRaises(HTTPException) as raised:
                        await server.discord_api("/users/@me/guilds", "test-token")
                self.assertEqual(raised.exception.status_code, expected)

    async def test_refresh_replaces_expired_access_token_in_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"DATABASE_PATH": str(Path(directory) / "test.sqlite3")}):
                server.db.initialize()
                with server.db.connect() as conn:
                    conn.execute(
                        "INSERT INTO sessions(session_hash,user_id,username,access_token,refresh_token,expires_at,created_at) VALUES (?,?,?,?,?,?,?)",
                        ("test-session", "123", "User", "old-token", "old-refresh", int(time.time()) - 20, int(time.time())),
                    )
                session = {"access_token": "old-token", "refresh_token": "old-refresh", "expires_at": 0}
                replacement = {"access_token": "new-token", "refresh_token": "new-refresh", "expires_in": 3600}
                with patch.object(server, "exchange_token", new=AsyncMock(return_value=replacement)):
                    await server.refresh_session(session, "test-session")
                self.assertEqual(session["access_token"], "new-token")
                with server.db.connect() as conn:
                    stored = conn.execute("SELECT access_token FROM sessions WHERE session_hash=?", ("test-session",)).fetchone()
                self.assertEqual(stored["access_token"], "new-token")


if __name__ == "__main__":
    unittest.main()
