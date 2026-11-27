"""Consistent SQLite backups that can be downloaded from the Fly Volume.

Usage inside a Fly Machine: python -m app.backup
Backups contain Discord OAuth tokens. Keep them private.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .db import database_path


def create_backup(destination: Path | None = None) -> Path:
    source = database_path()
    if not source.is_file():
        raise FileNotFoundError(f"Database not found: {source}")
    if destination is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = source.parent / "backups" / f"voice_hub_{stamp}.sqlite3"
    if destination.resolve() == source.resolve():
        raise ValueError("Backup path must be different from the live database")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise FileExistsError(f"Backup already exists: {destination}")
    with sqlite3.connect(source) as original, sqlite3.connect(destination) as backup:
        original.backup(backup)
    os.chmod(destination, 0o600)
    return destination


if __name__ == "__main__":
    print(create_backup())
