"""Thai speech synthesis with a resilient secondary provider.

Prefer the natural Edge Neural voice. Edge's unofficial read-aloud endpoint can
intermittently return NoAudioReceived, so fall back to Google Translate TTS only
when all Neural attempts fail. Both produce an MP3 for the same FFmpeg pipeline.
"""

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


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
    last_edge_error: Exception | None = None
    for attempt in range(3):
        path.unlink(missing_ok=True)
        try:
            await asyncio.wait_for(_edge_save(text, settings, path), timeout=20)
            _check_audio(path)
            return "Edge Neural"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            last_edge_error = error
            log.warning("Edge Neural TTS attempt %s failed: %s", attempt + 1, type(error).__name__)
            if attempt < 2:
                await asyncio.sleep(0.7 * (attempt + 1))

    path.unlink(missing_ok=True)
    try:
        await asyncio.wait_for(asyncio.to_thread(_google_save, text, path), timeout=25)
        _check_audio(path)
        log.warning("Using backup Thai TTS after Edge Neural failures")
        return "Google Thai (backup)"
    except asyncio.CancelledError:
        raise
    except Exception as fallback_error:
        path.unlink(missing_ok=True)
        edge_name = type(last_edge_error).__name__ if last_edge_error else "Unknown"
        fallback_name = type(fallback_error).__name__
        raise RuntimeError(f"Edge {edge_name}; backup {fallback_name}") from fallback_error
