"""Encryption at rest for private document content (Fernet: AES-128-CBC + HMAC)."""

import json
import os
import time
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet

from .config import get_settings


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.encryption_key
    if not key:
        if settings.environment == "production":
            raise RuntimeError("ENCRYPTION_KEY must be set in production.")
        # Development convenience: create and persist a key next to the data.
        key_file = Path(settings.storage_dir).parent / "encryption.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = Fernet.generate_key().decode()
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(key)
        except FileExistsError:
            # Already created (possibly a moment ago by another process).
            for _ in range(50):
                key = key_file.read_text().strip()
                if key:
                    break
                time.sleep(0.1)
    return Fernet(key.encode() if isinstance(key, str) else key)


def reset_key_cache() -> None:
    _fernet.cache_clear()


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _fernet().decrypt(token)


def encrypt_text(text: str) -> bytes:
    return encrypt_bytes(text.encode("utf-8"))


def decrypt_text(token: bytes) -> str:
    return decrypt_bytes(token).decode("utf-8")


def encrypt_json(value) -> bytes:
    return encrypt_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def decrypt_json(token: bytes):
    return json.loads(decrypt_bytes(token))
