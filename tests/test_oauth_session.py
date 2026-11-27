"""Discord errors and OAuth token refresh must not produce false expiry alerts."""

import os
import tempfile
import time
import unittest
from pathlib import Path
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
