"""One-shot, opt-in installation of the local bgutil PO-token backend.

Invoked by Wispbyte's pre-start deploy hook after the operator creates the
private data/youtube-pot.enabled marker. Never run pip or npm at every start.
"""

import logging
import os
import shutil
import subprocess
from pathlib import Path


log = logging.getLogger("voicehub.pot_setup")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MARKER = DATA / "youtube-pot.enabled"
REPOSITORY = DATA / "bgutil-ytdlp-pot-provider"
SERVER = REPOSITORY / "server"
STAMP = SERVER / ".lilin-installed-2.0.2"
SOURCE = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
VERSION = "2.0.2"


def _build_environment() -> dict[str, str]:
    """Keep Discord/OAuth/YouTube credentials out of third-party build steps."""
    permitted = ("PATH", "HOME", "TMPDIR", "DENO_DIR", "NO_COLOR", "SSL_CERT_FILE")
    return {key: os.environ[key] for key in permitted if key in os.environ}


def provision() -> bool:
    if not MARKER.is_file():
        return False
    if STAMP.is_file() and (SERVER / "node_modules").is_dir():
        return True

    deno = shutil.which("deno") or str(ROOT / ".runtime" / "deno")
    if not Path(deno).is_file() or not os.access(deno, os.X_OK):
        log.warning("Deno is not available for PO token setup; retry after next restart")
        return False
    git = shutil.which("git")
    if not git:
        log.warning("Git is unavailable for PO token setup")
        return False

    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.chmod(0o700)
    clean_env = _build_environment()
    try:
        if not (REPOSITORY / ".git").is_dir():
            if REPOSITORY.exists():
                log.warning("PO provider directory exists but is not a Git clone; refusing to overwrite")
                return False
            log.info("Fetching bgutil PO token backend release %s", VERSION)
            subprocess.run(
                [git, "clone", "--depth", "1", "--single-branch", "--branch", VERSION,
                 SOURCE, str(REPOSITORY)],
                cwd=str(ROOT), env=clean_env, check=True, timeout=75,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        if not SERVER.is_dir():
            log.warning("PO provider checkout does not contain server directory")
            return False

        # Do not execute a modified checkout or a mismatched release.
        release = subprocess.run(
            [git, "-C", str(REPOSITORY), "describe", "--tags", "--exact-match"],
            cwd=str(ROOT), env=clean_env, check=True, timeout=10,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if release.stdout.decode("ascii", errors="replace").strip() != VERSION:
            log.warning("PO provider checkout version differs from pinned release")
            return False

        log.info("Installing PO token backend JS dependencies once")
        subprocess.run(
            [deno, "install", "--allow-scripts=npm:canvas", "--frozen"],
            cwd=str(SERVER), env=clean_env, check=True, timeout=180,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if not (SERVER / "node_modules").is_dir():
            log.warning("PO provider dependencies were not created")
            return False
        STAMP.touch(mode=0o600)
        log.info("YouTube PO token backend installed; it will bind to localhost only")
        return True
    except (OSError, subprocess.SubprocessError) as error:
        # Third-party stderr may contain unwanted details. Keep logs concise.
        log.warning("PO token setup failed (%s); bot startup is unaffected", type(error).__name__)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    provision()
