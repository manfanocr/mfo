"""Crash-safe atomic file writes.

Writes go to a temporary file in the same directory, are flushed and ``fsync``'d, then
atomically swapped into place with :func:`os.replace`. A crash or error before the swap
leaves the original file untouched and removes the temp file, so readers never observe a
partially-written file (invariants I-1, I-5; NFR-10, NFR-11).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path``, creating parent directories as needed."""
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Atomically write ``text`` to ``path``."""
    atomic_write_bytes(path, text.encode(encoding))
