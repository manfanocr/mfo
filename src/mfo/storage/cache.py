"""Content-addressed cache for expensive intermediate stage outputs.

Entries are keyed by a content hash (see :mod:`mfo.storage.hashing`) and sharded into
subdirectories by the key prefix to keep directory sizes manageable. Writes are atomic, so a
crash never leaves a corrupt cache entry (NFR-7, NFR-8, NFR-10).
"""

from __future__ import annotations

from pathlib import Path

from mfo.storage.atomic import atomic_write_bytes


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, key: str) -> Path:
        """Return the on-disk path for ``key`` (sharded by the first two characters)."""
        if len(key) < 2:
            raise ValueError("cache key must be at least 2 characters")
        return self.root / key[:2] / key

    def has(self, key: str) -> bool:
        return self.path_for(key).is_file()

    def read_bytes(self, key: str) -> bytes:
        return self.path_for(key).read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        atomic_write_bytes(self.path_for(key), data)

    def get(self, key: str) -> bytes | None:
        """Return cached bytes for ``key`` or ``None`` if absent."""
        path = self.path_for(key)
        return path.read_bytes() if path.is_file() else None
