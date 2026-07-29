from __future__ import annotations

import base64
import hashlib
from pathlib import Path


def encode_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["encode_base64", "file_sha256"]
