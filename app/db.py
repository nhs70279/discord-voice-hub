"""SQLite persistence. A new connection per operation avoids cross-task transactions."""

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

VOICE_CHOICES = {
    "th-TH-PremwadeeNeural": "ลิลิน — เสียงผู้หญิงภาษาไทยแบบ Neural",
}
DEFAULT_VOICE = "th-TH-PremwadeeNeural"
MIN_RATE, MAX_RATE = -40, 40
MIN_PITCH, MAX_PITCH = -30, 30


def database_path() -> Path:
    return Path(os.environ.get("DATABASE_PATH", "voice_hub.sqlite3"))


@contextmanager
def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with connect() as db:
        db.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS preferences (
                user_id TEXT PRIMARY KEY, voice TEXT NOT NULL,
                rate INTEGER NOT NULL DEFAULT 0,
                pitch INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY, read_channel_id TEXT
            );
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL, guild_id TEXT NOT NULL,
                name TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                query TEXT NOT NULL, position INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                username TEXT NOT NULL, access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL, expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
        """)
        # Migrate data created by the initial version without losing playlists.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(preferences)")}
        if "rate" not in columns:
            db.execute("ALTER TABLE preferences ADD COLUMN rate INTEGER NOT NULL DEFAULT 0")
        if "pitch" not in columns:
            db.execute("ALTER TABLE preferences ADD COLUMN pitch INTEGER NOT NULL DEFAULT 0")
        # Keep one consistent Lilin voice, migrating old male/non-Thai settings.
        # Preserve users' speed, pitch, and playlist settings.
        db.execute(
            "UPDATE preferences SET voice=? WHERE voice<>?",
            (DEFAULT_VOICE, DEFAULT_VOICE),
        )
    try:
        database_path().chmod(0o600)
    except OSError:
        pass


def get_voice(user_id: int | str) -> str:
    return get_voice_config(user_id)["voice"]


def get_voice_config(user_id: int | str) -> dict:
    with connect() as db:
        row = db.execute("SELECT voice,rate,pitch FROM preferences WHERE user_id=?", (str(user_id),)).fetchone()
        if not row:
            return {"voice": DEFAULT_VOICE, "rate": 0, "pitch": 0}
        voice = row["voice"] if row["voice"] in VOICE_CHOICES else DEFAULT_VOICE
        return {"voice": voice, "rate": row["rate"], "pitch": row["pitch"]}


def set_voice(user_id: int | str, voice: str):
    current = get_voice_config(user_id)
    set_voice_config(user_id, voice, current["rate"], current["pitch"])


def set_voice_config(user_id: int | str, voice: str, rate: int, pitch: int):
    if voice not in VOICE_CHOICES:
        raise ValueError("ลิลินใช้เสียงผู้หญิงภาษาไทยเพียงเสียงเดียวค่ะ")
    if not isinstance(rate, int) or not MIN_RATE <= rate <= MAX_RATE:
        raise ValueError("ความเร็วในการพูดอยู่นอกช่วงที่กำหนด")
    if not isinstance(pitch, int) or not MIN_PITCH <= pitch <= MAX_PITCH:
        raise ValueError("ระดับเสียงอยู่นอกช่วงที่กำหนด")
    with connect() as db:
        db.execute(
            "INSERT INTO preferences(user_id,voice,rate,pitch) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET voice=excluded.voice,rate=excluded.rate,pitch=excluded.pitch",
            (str(user_id), voice, rate, pitch),
        )


def get_read_channel(guild_id: int | str) -> int | None:
    with connect() as db:
        row = db.execute("SELECT read_channel_id FROM guild_settings WHERE guild_id=?", (str(guild_id),)).fetchone()
        return int(row["read_channel_id"]) if row and row["read_channel_id"] else None


def set_read_channel(guild_id: int | str, channel_id: int | str | None):
    with connect() as db:
        db.execute("INSERT INTO guild_settings VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET read_channel_id=excluded.read_channel_id", (str(guild_id), str(channel_id) if channel_id else None))


def list_playlists(user_id: str, guild_id: str):
    with connect() as db:
        rows = db.execute("SELECT id,name,created_at FROM playlists WHERE owner_id=? AND guild_id=? ORDER BY created_at DESC,id DESC", (user_id, guild_id)).fetchall()
        return [dict(r) for r in rows]


def create_playlist(user_id: str, guild_id: str, name: str):
    with connect() as db:
        cur = db.execute("INSERT INTO playlists(owner_id,guild_id,name,created_at) VALUES (?,?,?,?)", (user_id, guild_id, name, int(time.time())))
        return cur.lastrowid


def get_playlist(user_id: str, guild_id: str, playlist_id: int):
    with connect() as db:
        playlist = db.execute("SELECT * FROM playlists WHERE id=? AND owner_id=? AND guild_id=?", (playlist_id, user_id, guild_id)).fetchone()
        if playlist is None:
            return None
        items = db.execute("SELECT id,query,position FROM playlist_items WHERE playlist_id=? ORDER BY position,id", (playlist_id,)).fetchall()
        return {"id": playlist["id"], "name": playlist["name"], "items": [dict(i) for i in items]}


def add_playlist_item(user_id: str, guild_id: str, playlist_id: int, query: str):
    with connect() as db:
        playlist = db.execute("SELECT id FROM playlists WHERE id=? AND owner_id=? AND guild_id=?", (playlist_id, user_id, guild_id)).fetchone()
        if playlist is None:
            return False
        next_position = db.execute("SELECT COALESCE(MAX(position)+1, 0) FROM playlist_items WHERE playlist_id=?", (playlist_id,)).fetchone()[0]
        if next_position >= 100:
            raise ValueError("หนึ่งเพลย์ลิสต์เพิ่มได้สูงสุด 100 เพลงค่ะ")
        db.execute("INSERT INTO playlist_items(playlist_id,query,position) VALUES (?,?,?)", (playlist_id, query, next_position))
        return True


def delete_playlist_item(user_id: str, guild_id: str, playlist_id: int, item_id: int):
    with connect() as db:
        cur = db.execute("DELETE FROM playlist_items WHERE id=? AND playlist_id=? AND EXISTS (SELECT 1 FROM playlists WHERE id=? AND owner_id=? AND guild_id=?)", (item_id, playlist_id, playlist_id, user_id, guild_id))
        return cur.rowcount > 0


def delete_playlist(user_id: str, guild_id: str, playlist_id: int):
    with connect() as db:
        cur = db.execute("DELETE FROM playlists WHERE id=? AND owner_id=? AND guild_id=?", (playlist_id, user_id, guild_id))
        return cur.rowcount > 0
