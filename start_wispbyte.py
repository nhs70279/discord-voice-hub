"""One-process startup for the free Wispbyte Python egg (bot + FastAPI).

Set PY_FILE=start_wispbyte.py and REQUIREMENTS_FILE=requirements.wispbyte.txt
in the Wispbyte Startup tab. Runtime downloads are cached in .runtime.
"""

import hashlib
import logging
import os
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path


log = logging.getLogger("voicehub.wispbyte")
ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
DATA = ROOT / "data"
DENO_ARCHIVE = "deno-x86_64-unknown-linux-gnu.zip"
DENO_URL = f"https://github.com/denoland/deno/releases/latest/download/{DENO_ARCHIVE}"


def pick_port(environment: dict[str, str] | None = None) -> int:
    """Wispbyte publishes its allocated primary port as SERVER_PORT."""
    env = os.environ if environment is None else environment
    raw = env.get("SERVER_PORT") or env.get("PORT") or "8080"
    port = int(raw)
    if not 1 <= port <= 65535:
        raise ValueError("SERVER_PORT must be a TCP port from 1 to 65535")
    return port


def configure_storage() -> None:
    os.umask(0o077)
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.chmod(0o700)
    os.environ.setdefault("DATABASE_PATH", str(DATA / "voice_hub.sqlite3"))
    os.environ.setdefault("COOKIE_SECURE", "true")
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    RUNTIME.chmod(0o700)
    os.environ["PATH"] = str(RUNTIME) + os.pathsep + os.environ.get("PATH", "")


def configure_ffmpeg() -> None:
    """Make pip's bundled FFmpeg discoverable as `ffmpeg` for discord.py."""
    if shutil.which("ffmpeg"):
        log.info("FFmpeg available")
        return
    try:
        import imageio_ffmpeg

        binary = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if not binary.is_file():
            raise FileNotFoundError(binary)
        executable = RUNTIME / "ffmpeg"
        if executable.is_symlink() or executable.exists():
            executable.unlink()
        executable.symlink_to(binary)
        log.info("Using FFmpeg from imageio-ffmpeg")
    except (ImportError, OSError, RuntimeError) as exc:
        log.error("FFmpeg unavailable; music and Thai TTS will be silent: %s", exc)


def configure_deno() -> None:
    """Install Deno to writable disk with SHA-256 verification, never as root."""
    if shutil.which("deno"):
        return
    executable = RUNTIME / "deno"
    if executable.is_file() and os.access(executable, os.X_OK):
        return

    try:
        with tempfile.TemporaryDirectory(prefix="deno-", dir=RUNTIME) as temporary:
            temp = Path(temporary)
            archive = temp / DENO_ARCHIVE
            checksum = temp / (DENO_ARCHIVE + ".sha256sum")
            urllib.request.urlretrieve(DENO_URL, archive)
            urllib.request.urlretrieve(DENO_URL + ".sha256sum", checksum)

            expected = checksum.read_text(encoding="utf-8").split()[0].lower()
            if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
                raise ValueError("Invalid Deno SHA-256 manifest")
            digest = hashlib.sha256()
            with archive.open("rb") as reader:
                for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError("Deno SHA-256 mismatch")

            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                if "deno" not in names:
                    raise ValueError("Deno archive does not contain executable")
                extracted = Path(bundle.extract("deno", path=temp))
            extracted.chmod(extracted.stat().st_mode | stat.S_IXUSR)
            extracted.replace(executable)
            executable.chmod(0o700)
            log.info("Verified Deno installed")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        # Discord and TTS should still start when GitHub download is unavailable.
        log.warning("Deno unavailable; YouTube may fail until it is installed: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    configure_storage()
    configure_ffmpeg()
    configure_deno()

    import uvicorn

    port = pick_port()
    log.info("Starting Lilin bot + dashboard on Wispbyte port %d", port)
    uvicorn.run("app.server:app", host="0.0.0.0", port=port, workers=1, loop="asyncio", http="h11")


if __name__ == "__main__":
    main()
