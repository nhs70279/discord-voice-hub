"""One consistent Thai voice per bot process.

Use reliable Google Thai by default. Operators can opt into the Edge Neural
female voice; never alternate voices mid-conversation after a failure.
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

EDGE_TIMEOUT_SECONDS = 12
GOOGLE_TIMEOUT_SECONDS = 15
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
    """Produce a single Thai voice; never concatenate or retry with another voice."""
    path.unlink(missing_ok=True)
    if os.getenv("LILIN_TTS_ENGINE", "google").strip().lower() == "edge":
        await asyncio.wait_for(_edge_save(text, settings, path), timeout=EDGE_TIMEOUT_SECONDS)
        engine = "Edge Neural"
    else:
        await asyncio.wait_for(asyncio.to_thread(_google_save, text, path), timeout=GOOGLE_TIMEOUT_SECONDS)
        engine = "Google Thai"
    _check_audio(path)
    return engine


async def warmup_thai() -> None:
    """Prime imports, worker thread, DNS and voice service without audible speech.

    Best effort: the bot and first chat must never wait for this request.
    """
    if os.getenv("LILIN_TTS_WARMUP", "1").strip().lower() in {"0", "false", "off"}:
        return
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="lilin-tts-warmup-") as folder:
            path = Path(folder) / "warmup.mp3"
            settings = {"voice": "th-TH-PremwadeeNeural", "rate": 0, "pitch": 0}
            await asyncio.wait_for(synthesize_thai("สวัสดีค่ะ", settings, path), timeout=GOOGLE_TIMEOUT_SECONDS)
        log.info("Thai TTS warmed up in %.2f seconds", time.monotonic() - started)
    except Exception as error:
        # Never change voices or delay Discord startup if the warm-up fails.
        log.warning("Thai TTS warm-up unavailable (%s); normal requests still work", type(error).__name__)
