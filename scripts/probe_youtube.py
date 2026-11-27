"""One-shot YouTube extraction + FFmpeg smoke test; no Discord or cookies needed.

Run only when the owner requests a probe. Never print media URLs, tokens, or
third-party response bodies into the publicly visible hosting console.
"""

import logging
import os
import shutil
import subprocess
import time

import yt_dlp

from app.player import _youtube_playback_options


log = logging.getLogger("voicehub.youtube_probe")
SAMPLE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def run_probe() -> bool:
    if os.getenv("YOUTUBE_POT_READY") != "1":
        log.warning("YouTube probe skipped: local PO token provider is not ready")
        return False

    started = time.monotonic()
    log.info("YouTube probe started: public 19-second sample, mweb client, no cookies")
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 8,
        "extractor_retries": 0,
        "retries": 0,
        **_youtube_playback_options(),
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(SAMPLE_URL, download=False)
    except yt_dlp.utils.DownloadError as error:
        message = str(error).lower()
        reason = "login verification" if "sign in to confirm" in message or "not a bot" in message else (
            "HTTP 403" if "403" in message else "extractor rejected the video"
        )
        log.warning("YouTube probe extraction failed: %s (%.1f seconds)", reason, time.monotonic() - started)
        return False
    except Exception as error:
        log.warning("YouTube probe extraction failed: %s", type(error).__name__)
        return False

    url = (info or {}).get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        log.warning("YouTube probe extraction returned no usable HTTPS audio stream")
        return False
    log.info("YouTube probe extraction OK (%.1f seconds); checking FFmpeg audio", time.monotonic() - started)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("YouTube probe cannot decode: FFmpeg is missing")
        return False
    # Retain only required stream headers, never emit their values into logs.
    headers = (info or {}).get("http_headers") or {}
    allowed = {"user-agent", "referer", "origin", "accept", "accept-language"}
    header_lines = "".join(
        f"{key}: {value}\r\n" for key, value in headers.items()
        if key.lower() in allowed and isinstance(value, str)
        and "\r" not in value and "\n" not in value
    )
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if header_lines:
        command.extend(["-headers", header_lines])
    command.extend(["-i", url, "-map", "0:a:0", "-t", "0.5", "-f", "null", "-"])
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=15, check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("YouTube probe FFmpeg timed out fetching the audio stream")
        return False
    except OSError as error:
        log.warning("YouTube probe FFmpeg could not launch: %s", type(error).__name__)
        return False
    if result.returncode:
        reason = "HTTP 403" if b"403" in result.stderr else "stream read/decoding failed"
        log.warning("YouTube probe FFmpeg failed: %s (exit %d)", reason, result.returncode)
        return False
    log.info("YouTube probe PASSED: extraction and FFmpeg audio read (%.1f seconds)", time.monotonic() - started)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(0 if run_probe() else 1)
