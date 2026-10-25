"""Private file storage for uploaded originals (already encrypted by the
caller). Keys are random hex strings; nothing user-controlled ever becomes
part of a path."""

import os
import re
import secrets
from pathlib import Path

from .config import get_settings

_KEY_RE = re.compile(r"^[0-9a-f]{40}$")


def _path(key: str) -> Path:
    if not _KEY_RE.match(key):
        raise ValueError("invalid storage key")
    return Path(get_settings().storage_dir) / key[:2] / key


def save_file(data: bytes) -> str:
    key = secrets.token_hex(20)
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return key


def read_file(key: str) -> bytes:
    return _path(key).read_bytes()


def delete_file(key: str | None) -> None:
    if not key:
        return
    try:
        _path(key).unlink(missing_ok=True)
    except ValueError:
        pass
