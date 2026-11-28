"""Voice mixing, FIFO TTS, and a separate music playback queue."""

import asyncio
import logging
import os
import re
import shlex
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import discord
import yt_dlp

from .audio_mix import FRAME_BYTES, SILENCE, diagnostic_tone_frame, mix_pcm, normalize
from .music_queue import MusicQueue
from .tts_text import speech_text
from .tts_fallback import synthesize_thai

log = logging.getLogger(__name__)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
MAX_LOCAL_AUDIO_BYTES = 80 * 1024 * 1024
MAX_TTS_WAIT_SECONDS = 30
YOUTUBE_TITLE_PENDING = "กำลังโหลดชื่อเพลง…"
YOUTUBE_CHALLENGE_COOLDOWN_SECONDS = 10 * 60
_youtube_challenge_lock = threading.Lock()
_youtube_challenge_until = 0.0


class YouTubeRequestTiming:
    """Estimate time between yt-dlp's existing request milestones.

    yt-dlp sends informational extraction messages to ``logger.debug`` even
    with ``quiet=True``. Store only fixed stage names and elapsed durations;
    never retain or log full messages, URLs, tokens, or cookie information.
    These are stage intervals, not per-HTTP-request timings.
    """

    _CLIENTS = ("mweb", "tv", "web safari", "web music", "web")

    def __init__(self):
        self.started = time.monotonic()
        self.events: list[tuple[str, float]] = []

    def debug(self, message: str):
        message = str(message).lower()
        stage = None
        if "downloading webpage" in message:
            stage = "webpage"
        elif "downloading m3u8 information" in message:
            stage = "hls"
        else:
            for client in self._CLIENTS:
                if f"downloading {client} player api json" in message:
                    stage = client.replace(" ", "_")
                    break
                if f"downloading {client} client config" in message:
                    stage = "config"
                    break
        if stage:
            self.events.append((stage, time.monotonic()))

    def warning(self, _message: str):
        pass  # Never write raw yt-dlp messages that may contain private URLs.

    def error(self, _message: str):
        pass

    def summary(self) -> dict[str, int]:
        totals: dict[str, float] = {}
        label = "เริ่มต้น"
        previous = self.started
        for stage, timestamp in self.events:
            totals[label] = totals.get(label, 0.0) + max(0.0, timestamp - previous)
            label, previous = stage, timestamp
        totals[label] = totals.get(label, 0.0) + max(0.0, time.monotonic() - previous)
        return {stage: round(seconds * 1000) for stage, seconds in totals.items()}


class MusicSourceUnavailable(RuntimeError):
    """A playback failure that can be explained to Discord users in Thai."""


def valid_query(query: str) -> bool:
    query = query.strip()
    if not 1 <= len(query) <= 400:
        return False
    parsed = urlparse(query)
    if parsed.scheme or parsed.netloc:
        return parsed.scheme == "https" and parsed.hostname in YOUTUBE_HOSTS
    return True  # Keyword searches always use YouTube


def _is_single_youtube_video(query: str) -> bool:
    """Avoid a redundant metadata request for direct single-video links.

    Playlists still need yt-dlp expansion; keyword searches still need YouTube.
    The playback worker resolves the title and audio stream in one operation.
    """
    parsed = urlparse(query)
    if parsed.hostname not in YOUTUBE_HOSTS:
        return False
    parameters = parse_qs(parsed.query)
    if "list" in parameters:
        return False
    if parsed.hostname == "youtu.be":
        video_id = parsed.path.strip("/")
    elif parsed.path == "/watch":
        video_id = parameters.get("v", [""])[0]
    elif parsed.path.startswith(("/shorts/", "/live/")):
        video_id = parsed.path.split("/")[2]
    else:
        return False
    return re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is not None


def _youtube_auth_options() -> dict:
    """Use optional private cookies only when explicitly configured by the operator."""
    path = os.getenv("YOUTUBE_COOKIES_PATH", "").strip()
    if not path:
        return {}
    cookie_file = Path(path)
    if not cookie_file.is_file() or cookie_file.stat().st_mode & 0o077:
        # Never load an openly readable cookie jar or print its contents.
        log.warning("YOUTUBE_COOKIES_PATH must point to an owner-only readable file")
        return {}
    return {"cookiefile": str(cookie_file)}


def _youtube_proxy_options() -> dict:
    """Optional operator-supplied egress. Never log proxy credentials."""
    raw = os.getenv("YOUTUBE_PROXY_URL", "").strip()
    if not raw:
        return {}
    try:
        parsed = urlparse(raw)
        valid = parsed.scheme in {"http", "https", "socks5", "socks5h"} and bool(parsed.hostname and parsed.port)
    except ValueError:
        valid = False
    if not valid:
        log.warning("YOUTUBE_PROXY_URL is not a valid HTTP(S) or SOCKS5 proxy URL")
        return {}
    return {"proxy": raw}


def _youtube_bot_challenge(error: Exception) -> bool:
    message = str(error).lower()
    return "sign in to confirm" in message or "not a bot" in message


def youtube_challenge_cooldown_seconds() -> int:
    """Avoid repeated requests once YouTube has asked this IP to verify."""
    with _youtube_challenge_lock:
        remaining = _youtube_challenge_until - time.monotonic()
    return max(0, int(remaining + 0.999))


def _record_youtube_challenge() -> None:
    global _youtube_challenge_until
    with _youtube_challenge_lock:
        _youtube_challenge_until = max(
            _youtube_challenge_until, time.monotonic() + YOUTUBE_CHALLENGE_COOLDOWN_SECONDS
        )
    log.warning("YouTube requested bot verification; pausing new YouTube requests for 10 minutes")


def _youtube_verification_message() -> str:
    return (
        "YouTube ขอให้ยืนยันว่าไม่ใช่บอทจากการเชื่อมต่อนี้ค่ะ "
        "ลิลินหยุดลอง YouTube ซ้ำชั่วคราวเพื่อลดการถูกจำกัดคำขอ "
        "กรุณาลองใหม่ภายหลังค่ะ"
    )


def _youtube_playback_options() -> dict:
    """Ask yt-dlp for YouTube clients in one extraction, without retry loops.

    The provider can supply a GVS token for mweb, but some hosting IPs receive
    LOGIN_REQUIRED before token generation. Other YouTube clients may still
    expose permitted formats. This never switches music platforms.
    """
    # This flag is set only after the local provider actually starts. Merely
    # installing the yt-dlp plugin does not create any PO tokens.
    provider_ready = os.getenv("YOUTUBE_POT_READY") == "1"
    return {
        "format": "bestaudio[protocol*=m3u8]/bestaudio/best",
        "extractor_args": {"youtube": {"player_client": (
            ["mweb", "tv", "web_safari"] if provider_ready else ["web_safari"]
        )}},
    }


def discover_tracks(query: str) -> list[dict]:
    """Resolve YouTube keywords, links, or playlists (30 max)."""
    if not valid_query(query):
        raise ValueError("กรุณาใส่ชื่อเพลงหรือลิงก์ YouTube แบบ HTTPS")
    if youtube_challenge_cooldown_seconds():
        raise MusicSourceUnavailable(_youtube_verification_message())
    if _is_single_youtube_video(query):
        return [{"title": YOUTUBE_TITLE_PENDING, "url": query}]
    settings = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 30,
        "default_search": "ytsearch1",
        # Do not disguise a YouTube verification error as an empty search.
        "ignoreerrors": False,
        "noplaylist": False,
        "socket_timeout": 10,
        "extractor_retries": 1,
        **_youtube_auth_options(),
        **_youtube_proxy_options(),
    }
    try:
        with yt_dlp.YoutubeDL(settings) as downloader:
            data = downloader.extract_info(query, download=False)
    except yt_dlp.utils.DownloadError as error:
        if _youtube_bot_challenge(error):
            _record_youtube_challenge()
            raise MusicSourceUnavailable(_youtube_verification_message()) from error
        raise
    if not data:
        return []
    raw = list(data.get("entries") or [data])
    result = []
    for item in raw[:30]:
        if not item:
            continue
        item_id = item.get("id")
        url = item.get("webpage_url") or item.get("url")
        if not isinstance(url, str) or not valid_query(url):
            url = f"https://www.youtube.com/watch?v={item_id}" if item_id else None
        if url and valid_query(url):
            result.append({"title": item.get("title") or "Unknown", "url": url})
    return result


def music_audio_source(url: str, *, title_callback: Callable[[str], None] | None = None,
                       timing_callback: Callable[[dict[str, int]], None] | None = None) -> discord.AudioSource:
    """Use a YouTube stream only. Never substitute a different recording."""
    if not valid_query(url) or urlparse(url).hostname not in YOUTUBE_HOSTS:
        raise ValueError("รองรับเฉพาะลิงก์ YouTube แบบ HTTPS ค่ะ")
    if youtube_challenge_cooldown_seconds():
        raise MusicSourceUnavailable(_youtube_verification_message())
    # yt-dlp must fetch the media bytes through the same egress as extraction.
    # FFmpeg's normal URL streaming would otherwise bypass the configured proxy.
    if _youtube_proxy_options():
        return downloaded_youtube_source(url, title_callback=title_callback, timing_callback=timing_callback)
    timing = YouTubeRequestTiming()
    options = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "socket_timeout": 10, "extractor_retries": 1,
        "logger": timing,
        **_youtube_playback_options(),
        **_youtube_auth_options(),
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as error:
        if _youtube_bot_challenge(error):
            _record_youtube_challenge()
            raise MusicSourceUnavailable(_youtube_verification_message()) from error
        raise
    finally:
        if timing_callback:
            timing_callback(timing.summary())
    if title_callback and info and isinstance(info.get("title"), str):
        title_callback(info["title"])
    return _ffmpeg_stream(info)


class TemporaryAudioSource(discord.AudioSource):
    """Close FFmpeg before deleting its private downloaded audio file."""

    def __init__(self, source: discord.AudioSource, directory: tempfile.TemporaryDirectory):
        self.source = source
        self.directory = directory

    def read(self) -> bytes:
        return self.source.read()

    def is_opus(self) -> bool:
        return self.source.is_opus()

    def cleanup(self):
        try:
            self.source.cleanup()
        finally:
            self.directory.cleanup()


def downloaded_youtube_source(url: str, *, title_callback: Callable[[str], None] | None = None,
                              timing_callback: Callable[[dict[str, int]], None] | None = None) -> discord.AudioSource:
    """Let yt-dlp fetch YouTube bytes itself when a proxy is configured."""
    if urlparse(url).hostname not in YOUTUBE_HOSTS:
        raise MusicSourceUnavailable("รองรับเฉพาะลิงก์ YouTube ค่ะ")
    if youtube_challenge_cooldown_seconds():
        raise MusicSourceUnavailable(_youtube_verification_message())

    def check_size(progress: dict):
        if progress.get("downloaded_bytes", 0) > MAX_LOCAL_AUDIO_BYTES:
            raise MusicSourceUnavailable("ไฟล์เสียง YouTube ใหญ่เกินขีดจำกัด 80 MiB ค่ะ")

    directory = tempfile.TemporaryDirectory(prefix="lilin-audio-")
    timing = YouTubeRequestTiming()
    try:
        settings = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 10, "extractor_retries": 1,
            "logger": timing,
            "outtmpl": str(Path(directory.name) / "audio.%(ext)s"),
            "max_filesize": MAX_LOCAL_AUDIO_BYTES,
            "progress_hooks": [check_size],
            **_youtube_playback_options(),
            **_youtube_auth_options(), **_youtube_proxy_options(),
        }
        with yt_dlp.YoutubeDL(settings) as downloader:
            info = downloader.extract_info(url, download=True)
            filename = downloader.prepare_filename(info) if info else None
        if title_callback and info and isinstance(info.get("title"), str):
            title_callback(info["title"])
        path = Path(filename) if filename else None
        if not path or not path.is_file() or path.stat().st_size > MAX_LOCAL_AUDIO_BYTES:
            raise MusicSourceUnavailable("ไม่สามารถดาวน์โหลดเสียง YouTube ได้ค่ะ")
        return TemporaryAudioSource(discord.FFmpegPCMAudio(str(path), options="-vn"), directory)
    except yt_dlp.utils.DownloadError as error:
        directory.cleanup()
        if _youtube_bot_challenge(error):
            _record_youtube_challenge()
            raise MusicSourceUnavailable(_youtube_verification_message()) from error
        raise
    except BaseException:
        directory.cleanup()
        raise
    finally:
        if timing_callback:
            timing_callback(timing.summary())


def _ffmpeg_stream(info: dict | None) -> discord.AudioSource:
    """Pass yt-dlp's required stream headers to FFmpeg without leaking others."""
    if not info or not info.get("url"):
        raise MusicSourceUnavailable("ไม่พบสตรีมเสียงสำหรับเพลงนี้ค่ะ")
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
    """PCM source that ducks music during speech and parks its sender when idle."""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_music_end, on_music_error=None, on_idle=None):
        self.loop = loop
        self.on_music_end = on_music_end
        self.on_music_error = on_music_error
        self.on_idle = on_idle
        self._lock = threading.RLock()
        self._idle_notified = False
        self.music: discord.AudioSource | None = None
        self.music_frames = 0
        self.music_frames_sent = 0
        self.speech_frames_sent = 0
        self.speech: deque[tuple[discord.AudioSource, Path | None, Callable[[float], None] | None]] = deque()
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
            self._idle_notified = False

    def skip(self):
        with self._lock:
            self._stop_music_locked()
            self.loop.call_soon_threadsafe(self.on_music_end)

    def _stop_music_locked(self):
        if self.music:
            self.music.cleanup()
            self.music = None

    def enqueue_speech(
        self, source: discord.AudioSource, path: Path | None, *, priority: bool = False,
        on_start: Callable[[float], None] | None = None,
    ):
        with self._lock:
            if self.closed:
                source.cleanup()
                if path:
                    path.unlink(missing_ok=True)
            else:
                entry = (source, path, on_start)
                if priority:
                    self.speech.appendleft(entry)
                else:
                    self.speech.append(entry)
                self._idle_notified = False

    def has_active_audio(self) -> bool:
        """Check if there is audio to send, without decoding or encoding a frame."""
        with self._lock:
            return not self.closed and (bool(self.speech) or (self.music is not None and not self.paused))

    def set_music_paused(self, paused: bool):
        with self._lock:
            self.paused = paused
            self._idle_notified = False

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
                source, path, on_start = self.speech[0]
                try:
                    frame = source.read()
                except Exception:
                    log.exception("Speech decoding failed")
                    frame = b""
                if frame:
                    speech = normalize(frame)
                    self.speech_frames_sent += 1
                    if on_start:
                        # Capture the first real PCM frame, not merely TTS generation.
                        self.speech[0] = (source, path, None)
                        self.loop.call_soon_threadsafe(on_start, time.monotonic())
                    break
                source.cleanup()
                if path:
                    path.unlink(missing_ok=True)
                self.speech.popleft()
            if speech != SILENCE:
                return mix_pcm(music, speech, self.music_volume * self.duck_fraction, self.tts_volume)
            if music != SILENCE:
                return mix_pcm(music, SILENCE, self.music_volume, 0)
            if not self.speech and (self.music is None or self.paused) and not self._idle_notified:
                self._idle_notified = True
                if self.on_idle:
                    self.loop.call_soon_threadsafe(self.on_idle)
            return SILENCE

    def cleanup(self):
        with self._lock:
            self.closed = True
            self._stop_music_locked()
            while self.speech:
                source, path, _ = self.speech.popleft()
                source.cleanup()
                if path:
                    path.unlink(missing_ok=True)


@dataclass
class Track:
    title: str
    url: str
    requester: str


@dataclass
class PreparedSpeech:
    path: Path
    engine: str
    started_at: float
    finished_at: float


@dataclass
class SpeechRequest:
    preparation: asyncio.Task[PreparedSpeech]
    queued_at: float
    received_at: float
    gateway_ms: int | None


class GuildPlayer:
    def __init__(self, vc: discord.VoiceClient, loop: asyncio.AbstractEventLoop):
        self.vc = vc
        self.loop = loop
        self.music_queue: MusicQueue[Track] = MusicQueue(max_size=150)
        self.tts_queue: asyncio.Queue[SpeechRequest] = asyncio.Queue(maxsize=8)
        # Download the next voice clip while the previous one is preparing.
        # Keep network concurrency low on a small Premium server.
        self._tts_slots = asyncio.Semaphore(2)
        self._tts_preparations: set[asyncio.Task[PreparedSpeech]] = set()
        self.music_done = asyncio.Event()
        self._music_generation = 0
        self.repeat_mode = "off"
        self.current: Track | None = None
        self.playback_status = "idle"
        self.last_music_search_ms: int | None = None
        self.last_music_prepare_ms: int | None = None
        self.last_music_stages: dict[str, int] | None = None
        self.last_music_error: str | None = None
        self.last_tts_error: str | None = None
        self.last_tts_engine: str | None = None
        self.last_tts_latency_ms: dict[str, int | None] | None = None
        self.tts_latency_samples: deque[int] = deque(maxlen=20)
        self.last_transport_error: str | None = None
        self.chat_events_seen = 0
        self.chat_events_without_content = 0
        self.chat_events_in_read_channel = 0
        self.chat_messages_enqueued = 0
        self.chat_messages_private_skipped = 0
        self.chat_messages_dropped = 0
        self.mixer = Mixer(loop, self.music_done.set, self._report_stream_failure, self._pause_audio_if_idle)
        self.music_task = loop.create_task(self._music_worker(), name=f"music-{vc.guild.id}")
        self.tts_task = loop.create_task(self._tts_worker(), name=f"tts-{vc.guild.id}")
        self.vc.play(self.mixer, after=self._on_audio_player_end)
        # discord.py otherwise Opus-encodes 50 silence frames/second forever,
        # even when nobody has queued music or speech. Free hosts cap CPU usage.
        self._pause_audio_if_idle()

    def _pause_audio_if_idle(self):
        if not self.mixer.has_active_audio() and self.vc.is_playing():
            self.vc.pause()

    def _resume_audio(self):
        if self.mixer.has_active_audio() and self.vc.is_paused():
            self.vc.resume()

    def pause_music(self):
        self.mixer.set_music_paused(True)
        self._pause_audio_if_idle()

    def resume_music(self):
        self.mixer.set_music_paused(False)
        self._resume_audio()

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
            self.last_music_stages = None
            generation = self._music_generation
            try:
                loading_started = time.monotonic()
                options = {"timing_callback": lambda stages: setattr(self, "last_music_stages", stages)}
                if track.title == YOUTUBE_TITLE_PENDING:
                    source = await asyncio.to_thread(
                        music_audio_source, track.url,
                        title_callback=lambda title: setattr(track, "title", title),
                        **options,
                    )
                else:
                    source = await asyncio.to_thread(music_audio_source, track.url, **options)
                self.last_music_prepare_ms = round((time.monotonic() - loading_started) * 1000)
                log.info("YouTube stream preparation guild=%s took %d ms", self.vc.guild.id,
                         self.last_music_prepare_ms)
                log.info("YouTube extraction stage intervals guild=%s stages=%s", self.vc.guild.id,
                         self.last_music_stages)
                if generation != self._music_generation:
                    source.cleanup()
                    continue
                self.music_done.clear()
                self.mixer.set_music(source)
                self.playback_status = "playing"
                self._resume_audio()
                await self.music_done.wait()
                if generation == self._music_generation and self.repeat_mode in {"one", "all"}:
                    self.music_queue.add_many([track], front=self.repeat_mode == "one")
            except asyncio.CancelledError:
                raise
            except MusicSourceUnavailable as error:
                log.warning("Could not play track %s: %s", track.title, error)
                self.last_music_error = str(error)
                self.playback_status = "error"
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
        self._resume_audio()

    async def _prepare_speech(self, message: str, settings: dict) -> PreparedSpeech:
        async with self._tts_slots:
            started_at = self.loop.time()
            fd, filename = tempfile.mkstemp(suffix=".mp3", prefix="discord-tts-")
            os.close(fd)
            path = Path(filename)
            try:
                # Shield the background network thread long enough to clean its
                # output if a queued message gets discarded during a request.
                synthesis = self.loop.create_task(synthesize_thai(message, settings, path))
                try:
                    engine = await asyncio.shield(synthesis)
                except asyncio.CancelledError:
                    try:
                        await synthesis
                    except Exception:
                        pass
                    raise
                return PreparedSpeech(path, engine, started_at, self.loop.time())
            except BaseException:
                path.unlink(missing_ok=True)
                raise

    @staticmethod
    def _discard_preparation(task: asyncio.Task[PreparedSpeech]):
        def cleanup(done: asyncio.Task[PreparedSpeech]):
            if done.cancelled():
                return
            try:
                done.result().path.unlink(missing_ok=True)
            except Exception:
                # A failed synthesis already removed its temporary file.
                pass

        if task.done():
            cleanup(task)
        else:
            task.add_done_callback(cleanup)
            task.cancel()

    async def _tts_worker(self):
        while True:
            request = await self.tts_queue.get()
            path: Path | None = None
            try:
                if self.loop.time() - request.queued_at > MAX_TTS_WAIT_SECONDS:
                    self.chat_messages_dropped += 1
                    self._discard_preparation(request.preparation)
                    continue
                self.last_tts_error = None
                prepared = await request.preparation
                path = prepared.path
                self.last_tts_engine = prepared.engine
                if self.loop.time() - request.queued_at > MAX_TTS_WAIT_SECONDS:
                    self.chat_messages_dropped += 1
                    continue
                source = discord.FFmpegPCMAudio(str(path), options="-vn")
                def record_first_frame(sent_at: float, *, queued=request.queued_at, received=request.received_at,
                                       started=prepared.started_at, generated=prepared.finished_at,
                                       gateway=request.gateway_ms):
                    self._record_tts_latency(received, queued, started, generated, sent_at, gateway)

                self.mixer.enqueue_speech(source, path, on_start=record_first_frame)
                self._resume_audio()
                path = None  # Mixer now owns this file
            except asyncio.CancelledError:
                self._discard_preparation(request.preparation)
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

    def _record_tts_latency(self, received_at: float, queued_at: float, started_at: float,
                            generated_at: float, sent_at: float, gateway_ms: int | None):
        metrics = {
            "discord": gateway_ms,
            "queue": round((started_at - queued_at) * 1000),
            "synthesis": round((generated_at - started_at) * 1000),
            "playback_wait": round((sent_at - generated_at) * 1000),
            "total": round((sent_at - received_at) * 1000) + (gateway_ms or 0),
        }
        self.last_tts_latency_ms = metrics
        self.tts_latency_samples.append(metrics["total"])
        log.info("TTS latency guild=%s gateway=%s queue=%d synthesis=%d playback_wait=%d total=%d ms",
                 self.vc.guild.id, metrics["discord"], metrics["queue"], metrics["synthesis"],
                 metrics["playback_wait"], metrics["total"])

    async def add_speech(self, message: str, settings: dict, *, received_at: float | None = None,
                         gateway_ms: int | None = None) -> bool:
        if self.tts_queue.full():
            # Prefer recent chat over speaking messages from minutes ago.
            discarded = self.tts_queue.get_nowait()
            self._discard_preparation(discarded.preparation)
            self.tts_queue.task_done()
            self.chat_messages_dropped += 1
        queued_at = self.loop.time()
        task = self.loop.create_task(self._prepare_speech(message, settings))
        self._tts_preparations.add(task)
        task.add_done_callback(self._tts_preparations.discard)
        self.tts_queue.put_nowait(SpeechRequest(task, queued_at, received_at or queued_at, gateway_ms))
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
        while not self.tts_queue.empty():
            pending = self.tts_queue.get_nowait()
            self._discard_preparation(pending.preparation)
            self.tts_queue.task_done()
        preparations = tuple(self._tts_preparations)
        for task in preparations:
            if not task.done():
                task.cancel()
        if preparations:
            await asyncio.gather(*preparations, return_exceptions=True)
        self.vc.stop()
        self.mixer.cleanup()
        if self.vc.is_connected():
            await self.vc.disconnect(force=True)
