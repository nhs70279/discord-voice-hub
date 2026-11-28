"""One-process startup for the free Wispbyte Python egg (bot + FastAPI).

Set PY_FILE=start_wispbyte.py and REQUIREMENTS_FILE=requirements.wispbyte.txt
in the Wispbyte Startup tab. Runtime downloads are cached in .runtime.
"""

import hashlib
import logging
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


log = logging.getLogger("voicehub.wispbyte")
ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
DATA = ROOT / "data"
DENO_ARCHIVE = "deno-x86_64-unknown-linux-gnu.zip"
DENO_URL = f"https://github.com/denoland/deno/releases/latest/download/{DENO_ARCHIVE}"
CLOUDFLARED_VERSION = "2026.11.2"
CLOUDFLARED_SHA256 = "56f13335143ec0e22772963290683a84a04114a35ec64cbc794a4d39ff9bb8cf"
CLOUDFLARED_URL = (
    f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/cloudflared-linux-amd64"
)


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


def configure_youtube_cookies() -> None:
    """Secure an explicitly configured cookie upload without reading its contents.

    Wispbyte's web uploader may create files readable by other users. Only
    tighten permissions on our dedicated data file, never arbitrary paths.
    """
    configured = os.getenv("YOUTUBE_COOKIES_PATH", "").strip()
    if not configured:
        return
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    if path != DATA / "youtube-cookies.txt":
        return
    if path.is_symlink():
        log.warning("Refusing a symlink for the YouTube cookie file")
        os.environ.pop("YOUTUBE_COOKIES_PATH", None)
        return
    if not path.is_file():
        log.info("YouTube cookies configured; waiting for a private file upload")
        return
    try:
        path.chmod(0o600)
    except OSError:
        log.warning("Could not secure permissions for the YouTube cookie file")
        os.environ.pop("YOUTUBE_COOKIES_PATH", None)
        return
    os.environ["YOUTUBE_COOKIES_PATH"] = str(path)
    log.info("YouTube cookie file is owner-only readable")


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


def configure_cloudflared() -> Path | None:
    """Prepare an optional, checksum-pinned Tunnel client without root access."""
    if not os.getenv("CLOUDFLARE_TUNNEL_TOKEN"):
        return None

    existing = shutil.which("cloudflared")
    if existing:
        return Path(existing)

    executable = RUNTIME / "cloudflared"
    if executable.is_file() and os.access(executable, os.X_OK):
        return executable

    try:
        with tempfile.TemporaryDirectory(prefix="cloudflared-", dir=RUNTIME) as temporary:
            downloaded = Path(temporary) / "cloudflared"
            urllib.request.urlretrieve(CLOUDFLARED_URL, downloaded)
            digest = hashlib.sha256()
            with downloaded.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != CLOUDFLARED_SHA256:
                raise ValueError("cloudflared SHA-256 mismatch")
            downloaded.chmod(0o700)
            downloaded.replace(executable)
            log.info("Verified cloudflared %s installed", CLOUDFLARED_VERSION)
            return executable
    except (OSError, ValueError) as exc:
        log.error("Cloudflare Tunnel cannot start: %s", exc)
        return None


def start_cloudflare_tunnel(executable: Path | None) -> subprocess.Popen | None:
    """Run the tunnel beside the bot; keep its credential out of process arguments."""
    token = os.getenv("CLOUDFLARE_TUNNEL_TOKEN", "")
    if not token or executable is None:
        return None
    environment = os.environ.copy()
    environment.pop("CLOUDFLARE_TUNNEL_TOKEN", None)
    environment["TUNNEL_TOKEN"] = token
    try:
        process = subprocess.Popen(
            [str(executable), "tunnel", "--protocol", "quic", "run"],
            env=environment,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.error("Could not launch Cloudflare Tunnel: %s", exc)
        return None
    log.info("Cloudflare Tunnel process started (pid %d)", process.pid)
    return process


def start_youtube_pot_provider() -> subprocess.Popen | None:
    """Optionally start an installed PO token server on localhost only.

    Startup never downloads or builds a JS project on the bot's small server.
    A missing/broken provider leaves the normal Safari YouTube path intact.
    """
    os.environ.pop("YOUTUBE_POT_READY", None)
    home = os.getenv("YOUTUBE_POT_SERVER_HOME", "").strip()
    if not home and (DATA / "youtube-pot.enabled").is_file():
        # The Wispbyte pre-start hook installs this once in private storage.
        home = str(DATA / "bgutil-ytdlp-pot-provider" / "server")
    if not home:
        return None
    configured = Path(home).expanduser()
    server = (configured if configured.is_absolute() else ROOT / configured).resolve()
    modules = server / "node_modules"
    main_file = server / "src" / "main.ts"
    deno = shutil.which("deno")
    if not deno or not modules.is_dir() or not main_file.is_file():
        log.warning("YouTube PO provider not installed; keeping Safari fallback")
        return None

    # The third-party provider needs network and JS permissions but does not
    # need the Discord token, OAuth secret, or browser cookies in its env.
    allowed_env = ("PATH", "HOME", "DENO_DIR", "TMPDIR", "NO_COLOR")
    environment = {key: os.environ[key] for key in allowed_env if key in os.environ}
    try:
        process = subprocess.Popen(
            [deno, "run", "--allow-env", "--allow-net", "--allow-ffi=.",
             "--allow-read=.", "../src/main.ts", "--host", "127.0.0.1", "--port", "4416"],
            cwd=str(modules), env=environment, stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.warning("YouTube PO provider could not start: %s", type(exc).__name__)
        return None
    # First launch on a small host may need to compile JS and load Canvas.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", 4416), timeout=0.15):
                os.environ["YOUTUBE_POT_READY"] = "1"
                log.info("YouTube PO token provider ready on localhost")
                return process
        except OSError:
            time.sleep(0.15)
    log.warning("YouTube PO provider did not become ready; using Safari fallback")
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=ROOT / ".env")
    configure_storage()
    configure_youtube_cookies()
    configure_ffmpeg()
    configure_deno()

    pot_provider = start_youtube_pot_provider()

    tunnel = start_cloudflare_tunnel(configure_cloudflared())

    import uvicorn

    port = pick_port()
    log.info("Starting Lilin bot + dashboard on Wispbyte port %d", port)
    try:
        uvicorn.run("app.server:app", host="0.0.0.0", port=port, workers=1, loop="asyncio", http="h11")
    finally:
        os.environ.pop("YOUTUBE_POT_READY", None)
        if pot_provider is not None and pot_provider.poll() is None:
            pot_provider.terminate()
            try:
                pot_provider.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pot_provider.kill()
                pot_provider.wait(timeout=3)
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)


if __name__ == "__main__":
    main()
