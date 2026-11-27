"""Voice mixing, FIFO TTS, and a separate music playback queue."""

import asyncio
import logging
import os
import shlex
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import discord
import yt_dlp

from .audio_mix import FRAME_BYTES, SILENCE, diagnostic_tone_frame, mix_pcm, normalize
from .music_queue import MusicQueue
from .tts_text import speech_text
from .tts_fallback import synthesize_thai

log = logging.getLogger(__name__)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def valid_query(query: str) -> bool:
    query = query.strip()
    if not 1 <= len(query) <= 400:
        return False
    parsed = urlparse(query)
    if parsed.scheme or parsed.netloc:
        return parsed.scheme == "https" and parsed.hostname in YOUTUBE_HOSTS
    return True  # Keywords are searched on YouTube


def discover_tracks(query: str) -> list[dict]:
    """Resolve keywords, individual YouTube links, or a YouTube playlist (30 max)."""
    if not valid_query(query):
        raise ValueError("กรุณาใส่ชื่อเพลงหรือลิงก์ YouTube แบบ HTTPS")
    settings = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 30,
        "default_search": "ytsearch1",
        "ignoreerrors": True,
        "noplaylist": False,
    }
    with yt_dlp.YoutubeDL(settings) as downloader:
        data = downloader.extract_info(query, download=False)
    if not data:
        return []
    raw = list(data.get("entries") or [data])
    result = []
    for item in raw[:30]:
        if not item:
            continue
        item_id = item.get("id")
        url = item.get("webpage_url") or item.get("url")
        if not url or not url.startswith("https://") or not valid_query(url):
            url = f"https://www.youtube.com/watch?v={item_id}" if item_id else None
        if url and valid_query(url):
            result.append({"title": item.get("title") or "Unknown", "url": url})
    return result


def music_audio_source(url: str) -> discord.AudioSource:
    """Acquire an ephemeral stream URL just before playback; never download media."""
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": "bestaudio/best", "noplaylist": True}) as downloader:
        info = downloader.extract_info(url, download=False)
    if not info or not info.get("url"):
        raise RuntimeError("Audio stream not found")
    # YouTube may require the same HTTP headers that yt-dlp used to resolve the
    # media URL. Without them FFmpeg can exit immediately with a 403, while the
    # bot remains in the voice channel and appears to be playing silence.
    headers = info.get("http_headers") or {}
    allowed = {"user-agent", "referer", "origin", "accept", "accept-language"}
    header_lines = "".join(
        f"{key}: {value}\r\n"
        for key, value in headers.items()
        if key.lower() in allowed and isinstance(value, str)
        and "\r" not in value and "\n" not in value
    )
    before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if header_lines:
        before_options += " -headers " + shlex.quote(header_lines)
    return discord.FFmpegPCMAudio(
        info["url"],
        before_options=before_options,
        options="-vn",
    )


class DiagnosticTone(discord.AudioSource):
    """A short local beep to test Discord audio independently of TTS and YouTube."""

    def __init__(self, duration_seconds: float = 4.0):
        self.frames_remaining = int(duration_seconds * 50)
        self.next_sample = 0

    def read(self) -> bytes:
        if self.frames_remaining <= 0:
            return b""
        frame = diagnostic_tone_frame(self.next_sample)
        self.next_sample += FRAME_BYTES // 4
        self.frames_remaining -= 1
        return frame

    def is_opus(self) -> bool:
        return False


class Mixer(discord.AudioSource):
    """Persistent PCM source; ducks music only while speech is audible."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_music_end, on_music_error=None):
        self.loop = loop
        self.on_music_end = on_music_end
        self.on_music_error = on_music_error
        self._lock = threading.RLock()
        self.music: discord.AudioSource | None = None
        self.music_frames = 0
        self.music_frames_sent = 0
        self.speech_frames_sent = 0
        self.speech: deque[tuple[discord.AudioSource, Path | None]] = deque()
        self.music_volume = 0.75
        self.tts_volume = 1.0
        self.duck_fraction = 0.28
        self.paused = False
        self.closed = False

    def is_opus(self):
        return False

    def set_music(self, source: discord.AudioSource):
        with self._lock:
            if self.closed:
                source.cleanup()
                return
            self._stop_music_locked()
            self.music = source
            self.music_frames = 0
            self.paused = False

    def skip(self):
        with self._lock:
            self._stop_music_locked()
            self.loop.call_soon_threadsafe(self.on_music_end)

    def _stop_music_locked(self):
        if self.music:
            self.music.cleanup()
            self.music = None

    def enqueue_speech(self, source: discord.AudioSource, path: Path | None, *, priority: bool = False):
        with self._lock:
            if self.closed:
                source.cleanup()
                if path:
                    path.unlink(missing_ok=True)
            else:
                if priority:
                    self.speech.appendleft((source, path))
                else:
                    self.speech.append((source, path))

    def read(self) -> bytes:
        with self._lock:
            if self.closed:
                return b""
            music = SILENCE
            speech = SILENCE
            if self.music and not self.paused:
                try:
                    frame = self.music.read()
                except Exception:
                    log.exception("Music decoding failed")
                    frame = b""
                if frame:
                    music = normalize(frame)
                    self.music_frames += 1
                    self.music_frames_sent += 1
                else:
                    if self.music_frames == 0 and self.on_music_error:
                        self.loop.call_soon_threadsafe(self.on_music_error)
                    self._stop_music_locked()
                    self.loop.call_soon_threadsafe(self.on_music_end)
            while self.speech:
                source, path = self.speech[0]
                try:
                    frame = source.read()
                except Exception:
                    log.exception("Speech decoding failed")
                    frame = b""
                if frame:
                    speech = normalize(frame)
                    self.speech_frames_sent += 1
                    break
                source.cleanup()
                if path:
                    path.unlink(missing_ok=True)
                self.speech.popleft()
            if speech != SILENCE:
                return mix_pcm(music, speech, self.music_volume * self.duck_fraction, self.tts_volume)
            if music != SILENCE:
                return mix_pcm(music, SILENCE, self.music_volume, 0)
            return SILENCE

    def cleanup(self):
        with self._lock:
            self.closed = True
            self._stop_music_locked()
            while self.speech:
                source, path = self.speech.popleft()
                source.cleanup()
                if path:
                    path.unlink(missing_ok=True)


@dataclass
class Track:
    title: str
    url: str
    requester: str


class GuildPlayer:
    def __init__(self, vc: discord.VoiceClient, loop: asyncio.AbstractEventLoop):
        self.vc = vc
        self.loop = loop
        self.music_queue: MusicQueue[Track] = MusicQueue(max_size=150)
        self.tts_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=40)
        self.music_done = asyncio.Event()
        self._music_generation = 0
        self.repeat_mode = "off"
        self.current: Track | None = None
        self.playback_status = "idle"
        self.last_music_error: str | None = None
        self.last_tts_error: str | None = None
        self.last_tts_engine: str | None = None
        self.last_transport_error: str | None = None
        self.chat_events_seen = 0
        self.chat_events_without_content = 0
        self.chat_events_in_read_channel = 0
        self.chat_messages_enqueued = 0
        self.chat_messages_private_skipped = 0
        self.mixer = Mixer(loop, self.music_done.set, self._report_stream_failure)
        self.music_task = loop.create_task(self._music_worker(), name=f"music-{vc.guild.id}")
        self.tts_task = loop.create_task(self._tts_worker(), name=f"tts-{vc.guild.id}")
        self.vc.play(self.mixer, after=self._on_audio_player_end)

    def _on_audio_player_end(self, error: Exception | None):
        if error:
            self.last_transport_error = type(error).__name__
            log.error("Discord audio sender stopped with an error: %r", error)

    def add_tracks(self, tracks: list[Track], *, front: bool = False, now: bool = False):
        self.music_queue.add_many(tracks, front=front)
        if now:
            self.skip()

    async def _music_worker(self):
        while True:
            track = await self.music_queue.get()
            self.current = track
            self.playback_status = "loading"
            self.last_music_error = None
            generation = self._music_generation
            try:
                source = await asyncio.to_thread(music_audio_source, track.url)
                if generation != self._music_generation:
                    source.cleanup()
                    continue
                self.music_done.clear()
                self.mixer.set_music(source)
                self.playback_status = "playing"
                await self.music_done.wait()
                if generation == self._music_generation and self.repeat_mode in {"one", "all"}:
                    self.music_queue.add_many([track], front=self.repeat_mode == "one")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Could not play track: %s", track.title)
                self.last_music_error = "ไม่สามารถโหลดเสียงจาก YouTube ได้ กรุณาลองเพลงอื่นหรือตรวจสอบบันทึกระบบ"
                self.playback_status = "error"
            finally:
                self.current = None
                if self.playback_status != "error":
                    self.playback_status = "idle"

    def _report_stream_failure(self):
        log.error("FFmpeg exited without producing any audio for %s", self.current.title if self.current else "unknown track")
        self.last_music_error = "เปิดสตรีมเสียงไม่สำเร็จ กรุณาลองเพลงอื่นหรือตรวจสอบบันทึกระบบ"
        self.playback_status = "error"

    def play_test_tone(self):
        """Play a network-free tone using the same PCM mixer and Discord voice path."""
        self.mixer.enqueue_speech(DiagnosticTone(), None, priority=True)

    async def _tts_worker(self):
        while True:
            message, settings = await self.tts_queue.get()
            path: Path | None = None
            try:
                self.last_tts_error = None
                fd, filename = tempfile.mkstemp(suffix=".mp3", prefix="discord-tts-")
                os.close(fd)
                path = Path(filename)
                self.last_tts_engine = await synthesize_thai(message, settings, path)
                source = discord.FFmpegPCMAudio(str(path), options="-vn")
                self.mixer.enqueue_speech(source, path)
                path = None  # Mixer now owns this file
            except asyncio.CancelledError:
                raise
            except Exception as error:
                status = getattr(error, "status", None)
                details = str(error)[:120] if isinstance(error, RuntimeError) else ""
                self.last_tts_error = (
                    f"{type(error).__name__} (HTTP {status})" if status
                    else f"{type(error).__name__}: {details}" if details
                    else type(error).__name__
                )
                log.exception("TTS failed")
            finally:
                if path:
                    path.unlink(missing_ok=True)
                self.tts_queue.task_done()

    async def add_speech(self, message: str, settings: dict) -> bool:
        if self.tts_queue.full():
            return False
        self.tts_queue.put_nowait((message, settings))
        return True

    def skip(self):
        self._music_generation += 1
        self.mixer.skip()

    def stop_music(self):
        self.music_queue.clear()
        self.skip()

    async def shutdown(self):
        self.music_task.cancel()
        self.tts_task.cancel()
        await asyncio.gather(self.music_task, self.tts_task, return_exceptions=True)
        self.vc.stop()
        self.mixer.cleanup()
        if self.vc.is_connected():
            await self.vc.disconnect(force=True)
