"""Discord OAuth2 protected dashboard and built-in Bot runner."""

import asyncio
import hashlib
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit

import httpx
import discord
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .bot import VoiceHubBot

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

OAUTH_API = "https://discord.com/api/v10"
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://127.0.0.1:8000/auth/callback")
SECURE_COOKIES = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ALLOWED_ORIGIN = f"{urlsplit(REDIRECT_URI).scheme}://{urlsplit(REDIRECT_URI).netloc}"
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.initialize()
    bot = VoiceHubBot()
    app.state.bot = bot
    token = os.getenv("DISCORD_TOKEN", "")
    task = None
    if token and token != "replace_with_bot_token":
        task = asyncio.create_task(bot.start(token), name="discord-bot")
    else:
        log.warning("DISCORD_TOKEN is missing; dashboard runs in setup mode")
    yield
    await bot.close()
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="ลิลิน — ผู้ช่วยเสียงภาษาไทย", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/healthz", include_in_schema=False)
async def healthz(request: Request):
    """Lightweight check for the local VM and tunnel; never returns secrets."""
    bot = getattr(request.app.state, "bot", None)
    return {"ok": True, "discord_connected": bool(bot and bot.is_ready())}


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(("/api/", "/auth/logout")):
        if request.headers.get("origin") != ALLOWED_ORIGIN:
            return JSONResponse({"detail": "ไม่สามารถตรวจสอบต้นทางของคำขอได้"}, status_code=403)
    return await call_next(request)


@app.get("/")
async def home():
    return FileResponse(INDEX)


@app.get("/auth/login")
async def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(503, "ยังไม่ได้ตั้งค่าการเข้าสู่ระบบด้วย Discord")
    state = secrets.token_urlsafe(30)
    url = "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify guilds",
        "state": state,
    })
    response = RedirectResponse(url)
    response.set_cookie("vh_oauth_state", state, max_age=600, httponly=True, secure=SECURE_COOKIES, samesite="lax")
    return response


async def exchange_token(data: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{OAUTH_API}/oauth2/token",
            data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, **data},
        )
        if response.status_code >= 400:
            log.warning("Discord OAuth error %s", response.status_code)
            raise HTTPException(401, "ยืนยันตัวตนกับ Discord ไม่สำเร็จ")
        return response.json()


async def discord_api(path: str, access_token: str):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{OAUTH_API}{path}", headers={"Authorization": f"Bearer {access_token}"})
        if response.status_code >= 400:
            raise HTTPException(401, "เซสชัน Discord หมดอายุ กรุณาเข้าสู่ระบบใหม่")
        return response.json()


@app.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    expected = request.cookies.get("vh_oauth_state", "")
    if not code or not state or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "ข้อมูลการเข้าสู่ระบบไม่ถูกต้อง")
    token = await exchange_token({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI})
    person = await discord_api("/users/@me", token["access_token"])
    session_id = secrets.token_urlsafe(40)
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    now = int(time.time())
    with db.connect() as conn:
        conn.execute("""INSERT INTO sessions(session_hash,user_id,username,access_token,refresh_token,expires_at,created_at)
                        VALUES (?,?,?,?,?,?,?)""", (
            session_hash, str(person["id"]), person["global_name"] or person["username"],
            token["access_token"], token["refresh_token"], now + int(token["expires_in"]), now,
        ))
        conn.execute("DELETE FROM sessions WHERE created_at < ?", (now - 30 * 86400,))
    result = RedirectResponse("/")
    result.delete_cookie("vh_oauth_state")
    result.set_cookie("vh_session", session_id, max_age=30 * 86400, secure=SECURE_COOKIES, httponly=True, samesite="lax")
    return result


async def require_session(request: Request):
    raw = request.cookies.get("vh_session", "")
    if not raw:
        raise HTTPException(401, "กรุณาเข้าสู่ระบบก่อนค่ะ")
    session_hash = hashlib.sha256(raw.encode()).hexdigest()
    with db.connect() as conn:
        item = conn.execute("SELECT * FROM sessions WHERE session_hash=?", (session_hash,)).fetchone()
    if item is None or int(item["created_at"]) < int(time.time()) - 30 * 86400:
        raise HTTPException(401, "กรุณาเข้าสู่ระบบก่อนค่ะ")
    session = dict(item)
    if session["expires_at"] <= int(time.time()) + 90:
        token = await exchange_token({"grant_type": "refresh_token", "refresh_token": session["refresh_token"]})
        session.update({"access_token": token["access_token"], "refresh_token": token["refresh_token"], "expires_at": int(time.time()) + int(token["expires_in"])})
        with db.connect() as conn:
            conn.execute("UPDATE sessions SET access_token=?,refresh_token=?,expires_at=? WHERE session_hash=?", (session["access_token"], session["refresh_token"], session["expires_at"], session_hash))
    return session


@app.post("/auth/logout")
async def logout(request: Request):
    session_id = request.cookies.get("vh_session")
    if session_id:
        with db.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_hash=?", (hashlib.sha256(session_id.encode()).hexdigest(),))
    result = JSONResponse({"ok": True})
    result.delete_cookie("vh_session")
    return result


async def user_guilds(request: Request, session=Depends(require_session)):
    available = await discord_api("/users/@me/guilds", session["access_token"])
    live_guild_ids = {str(g.id) for g in request.app.state.bot.guilds}
    return [g for g in available if str(g["id"]) in live_guild_ids]


async def require_guild(guild_id: str, guilds=Depends(user_guilds)):
    if not guild_id.isdigit():
        raise HTTPException(400, "รหัสเซิร์ฟเวอร์ไม่ถูกต้อง")
    found = next((g for g in guilds if g["id"] == guild_id), None)
    if not found:
        raise HTTPException(403, "คุณไม่ได้เป็นสมาชิกของเซิร์ฟเวอร์ที่มีลิลิน")
    return found


@app.get("/api/me")
async def get_me(request: Request, session=Depends(require_session), guilds=Depends(user_guilds)):
    bot = request.app.state.bot
    return {
        "id": session["user_id"], "name": session["username"],
        "voice_settings": db.get_voice_config(session["user_id"]), "voices": db.VOICE_CHOICES,
        "guilds": [{"id": g["id"], "name": g["name"], "admin": bool(int(g["permissions"]) & 0x28), "connected": int(g["id"]) in bot.guild_players} for g in guilds],
        "bot_online": bot.is_ready(),
    }


class VoiceRequest(BaseModel):
    voice: str
    rate: int = Field(ge=db.MIN_RATE, le=db.MAX_RATE)
    pitch: int = Field(ge=db.MIN_PITCH, le=db.MAX_PITCH)


@app.put("/api/me/voice")
async def update_voice(data: VoiceRequest, session=Depends(require_session)):
    try:
        db.set_voice_config(session["user_id"], data.voice, data.rate, data.pitch)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True}


@app.get("/api/guilds/{guild_id}/playlists")
async def playlists(guild_id: str, session=Depends(require_session), guild=Depends(require_guild)):
    return db.list_playlists(session["user_id"], guild_id)


class PlaylistName(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@app.post("/api/guilds/{guild_id}/playlists")
async def add_playlist(guild_id: str, data: PlaylistName, session=Depends(require_session), guild=Depends(require_guild)):
    return {"id": db.create_playlist(session["user_id"], guild_id, data.name.strip())}


@app.get("/api/guilds/{guild_id}/playlists/{playlist_id}")
async def playlist_detail(guild_id: str, playlist_id: int, session=Depends(require_session), guild=Depends(require_guild)):
    result = db.get_playlist(session["user_id"], guild_id, playlist_id)
    if result is None:
        raise HTTPException(404, "ไม่พบเพลย์ลิสต์")
    return result


class TrackRequest(BaseModel):
    query: str = Field(min_length=1, max_length=400)


class QueueRequest(TrackRequest):
    mode: Literal["normal", "next", "now"] = "normal"


class QueueMoveRequest(BaseModel):
    from_number: int = Field(ge=1, le=150)
    to_number: int = Field(ge=1, le=150)


def web_voice_player(request: Request, guild_id: str, user_id: str):
    player = request.app.state.bot.guild_players.get(int(guild_id))
    if not player or not player.vc.is_connected():
        raise HTTPException(409, "กรุณาพิมพ์ /join ใน Discord ก่อนค่ะ")
    if not any(str(member.id) == user_id for member in player.vc.channel.members):
        raise HTTPException(403, "กรุณาเข้าห้องเสียงเดียวกับลิลินค่ะ")
    return player


@app.get("/api/guilds/{guild_id}/queue")
async def get_queue(request: Request, guild_id: str, guild=Depends(require_guild)):
    player = request.app.state.bot.guild_players.get(int(guild_id))
    if not player or not player.vc.is_connected():
        return {"connected": False, "now_playing": None, "upcoming": [], "paused": False, "repeat": "off"}
    return {
        "connected": True,
        "now_playing": {"title": player.current.title, "requester": player.current.requester} if player.current else None,
        "upcoming": [{"number": index, "title": track.title, "requester": track.requester} for index, track in enumerate(player.music_queue.snapshot(), 1)],
        "paused": player.mixer.paused,
        "repeat": player.repeat_mode,
    }


@app.post("/api/guilds/{guild_id}/queue")
async def add_to_queue(request: Request, guild_id: str, data: QueueRequest, session=Depends(require_session), guild=Depends(require_guild)):
    web_voice_player(request, guild_id, session["user_id"])
    try:
        count = await request.app.state.bot.enqueue_query(int(guild_id), data.query, session["user_id"], front=data.mode == "next", now=data.mode == "now")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"queued": count}


@app.delete("/api/guilds/{guild_id}/queue/{position}")
async def remove_from_queue(request: Request, guild_id: str, position: int, session=Depends(require_session), guild=Depends(require_guild)):
    player = web_voice_player(request, guild_id, session["user_id"])
    try:
        track = player.music_queue.remove(position)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"removed": track.title}


@app.post("/api/guilds/{guild_id}/queue/move")
async def move_in_queue(request: Request, guild_id: str, data: QueueMoveRequest, session=Depends(require_session), guild=Depends(require_guild)):
    player = web_voice_player(request, guild_id, session["user_id"])
    try:
        player.music_queue.move(data.from_number, data.to_number)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"ok": True}


@app.post("/api/guilds/{guild_id}/queue/command/{command}")
async def queue_command(request: Request, guild_id: str, command: str, session=Depends(require_session), guild=Depends(require_guild)):
    player = web_voice_player(request, guild_id, session["user_id"])
    if command == "skip":
        player.skip()
    elif command == "clear":
        player.music_queue.clear()
    elif command == "stop":
        player.stop_music()
    elif command == "shuffle":
        player.music_queue.shuffle()
    elif command == "pause":
        player.mixer.paused = True
    elif command == "resume":
        player.mixer.paused = False
    elif command in {"repeat_off", "repeat_one", "repeat_all"}:
        player.repeat_mode = command.removeprefix("repeat_")
    else:
        raise HTTPException(400, "ไม่รองรับคำสั่งนี้")
    return {"ok": True}


@app.post("/api/guilds/{guild_id}/playlists/{playlist_id}/items")
async def add_item(guild_id: str, playlist_id: int, data: TrackRequest, session=Depends(require_session), guild=Depends(require_guild)):
    from .player import valid_query
    if not valid_query(data.query):
        raise HTTPException(400, "กรุณาใส่ชื่อเพลงหรือลิงก์ YouTube แบบ HTTPS")
    try:
        success = db.add_playlist_item(session["user_id"], guild_id, playlist_id, data.query.strip())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not success:
        raise HTTPException(404, "ไม่พบเพลย์ลิสต์")
    return {"ok": True}


@app.delete("/api/guilds/{guild_id}/playlists/{playlist_id}/items/{item_id}")
async def remove_item(guild_id: str, playlist_id: int, item_id: int, session=Depends(require_session), guild=Depends(require_guild)):
    if not db.delete_playlist_item(session["user_id"], guild_id, playlist_id, item_id):
        raise HTTPException(404, "ไม่พบเพลง")
    return {"ok": True}


@app.delete("/api/guilds/{guild_id}/playlists/{playlist_id}")
async def remove_playlist(guild_id: str, playlist_id: int, session=Depends(require_session), guild=Depends(require_guild)):
    if not db.delete_playlist(session["user_id"], guild_id, playlist_id):
        raise HTTPException(404, "ไม่พบเพลย์ลิสต์")
    return {"ok": True}


@app.post("/api/guilds/{guild_id}/playlists/{playlist_id}/play")
async def play_playlist(request: Request, guild_id: str, playlist_id: int, session=Depends(require_session), guild=Depends(require_guild)):
    record = db.get_playlist(session["user_id"], guild_id, playlist_id)
    if not record:
        raise HTTPException(404, "ไม่พบเพลย์ลิสต์")
    bot = request.app.state.bot
    web_voice_player(request, guild_id, session["user_id"])
    count = 0
    for item in record["items"]:
        try:
            count += await bot.enqueue_query(int(guild_id), item["query"], session["user_id"])
        except Exception:
            log.exception("Playlist item could not be queued")
    return {"queued": count}


@app.get("/api/guilds/{guild_id}/settings")
async def guild_settings(guild_id: str, guild=Depends(require_guild)):
    return {"read_channel_id": db.get_read_channel(guild_id)}


@app.get("/api/guilds/{guild_id}/channels")
async def guild_channels(request: Request, guild_id: str, guild=Depends(require_guild)):
    bot_guild = request.app.state.bot.get_guild(int(guild_id))
    if not bot_guild:
        return []
    return [{"id": str(c.id), "name": c.name} for c in bot_guild.text_channels]


class ChannelSetting(BaseModel):
    channel_id: str


@app.put("/api/guilds/{guild_id}/settings")
async def update_guild_settings(request: Request, guild_id: str, data: ChannelSetting, guild=Depends(require_guild)):
    if not (int(guild["permissions"]) & 0x28):
        raise HTTPException(403, "ต้องมีสิทธิ์จัดการเซิร์ฟเวอร์ค่ะ")
    bot_guild = request.app.state.bot.get_guild(int(guild_id))
    channel = bot_guild.get_channel(int(data.channel_id)) if bot_guild and data.channel_id.isdigit() else None
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(400, "กรุณาเลือกช่องข้อความที่ถูกต้อง")
    db.set_read_channel(guild_id, channel.id)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.server:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
