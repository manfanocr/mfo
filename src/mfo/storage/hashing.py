"""Content hashing helpers for the cache and reproducibility.

Stable SHA-256 hashes let the pipeline skip stages whose inputs are unchanged (NFR-7, NFR-8)
and record exactly what produced each output (NFR-26, NFR-27).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_key(*parts: str | bytes) -> str:
    """Hash an ordered list of parts into a single hex key.

    Each part is length-prefixed so that, e.g., ``("a", "b")`` and ``("ab", "")`` produce
    distinct keys.
    """
    digest = hashlib.sha256()
    for part in parts:
        raw = part.encode("utf-8") if isinstance(part, str) else part
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()
