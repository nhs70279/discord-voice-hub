"""Thai speech synthesis with a resilient secondary provider.

Prefer the natural Edge Neural voice. When the unofficial read-aloud endpoint
fails, briefly avoid it so a burst of chat messages doesn't each wait for a
broken service. Both providers produce MP3 for the same FFmpeg pipeline.
"""

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)
EDGE_TIMEOUT_SECONDS = 8
EDGE_COOLDOWN_SECONDS = 180
GOOGLE_TIMEOUT_SECONDS = 15
_edge_retry_at = 0.0


async def _edge_save(text: str, settings: dict, path: Path):
    import edge_tts

    await edge_tts.Communicate(
        text=text,
        voice=settings["voice"],
        rate=f'{settings["rate"]:+d}%',
        pitch=f'{settings["pitch"]:+d}Hz',
    ).save(str(path))


def _google_save(text: str, path: Path):
    from gtts import gTTS

    gTTS(text=text, lang="th", lang_check=False, timeout=10).save(str(path))


def _check_audio(path: Path):
    if not path.is_file() or path.stat().st_size < 256:
        raise RuntimeError("TTS produced no audio")


async def synthesize_thai(text: str, settings: dict, path: Path) -> str:
    """Write spoken Thai to path; return the actual synthesis engine used."""
    global _edge_retry_at
    last_edge_error: Exception | None = None
    now = asyncio.get_running_loop().time()
    if now >= _edge_retry_at:
        path.unlink(missing_ok=True)
        try:
            await asyncio.wait_for(_edge_save(text, settings, path), timeout=EDGE_TIMEOUT_SECONDS)
            _check_audio(path)
            _edge_retry_at = 0.0
            return "Edge Neural"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            last_edge_error = error
            _edge_retry_at = asyncio.get_running_loop().time() + EDGE_COOLDOWN_SECONDS
            log.warning("Edge Neural TTS unavailable (%s); using backup for %ss",
                        type(error).__name__, EDGE_COOLDOWN_SECONDS)

    path.unlink(missing_ok=True)
    try:
        await asyncio.wait_for(asyncio.to_thread(_google_save, text, path), timeout=GOOGLE_TIMEOUT_SECONDS)
        _check_audio(path)
        log.warning("Using backup Thai TTS after Edge Neural failures")
        return "Google Thai (backup)"
    except asyncio.CancelledError:
        raise
    except Exception as fallback_error:
        path.unlink(missing_ok=True)
        edge_name = type(last_edge_error).__name__ if last_edge_error else "CoolingDown"
        fallback_name = type(fallback_error).__name__
        raise RuntimeError(f"Edge {edge_name}; backup {fallback_name}") from fallback_error
