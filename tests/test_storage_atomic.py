"""Tests for crash-safe atomic writes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mfo.storage.atomic import atomic_write_bytes, atomic_write_text


def test_atomic_write_creates_then_replaces(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "v1")
    assert target.read_text() == "v1"
    atomic_write_text(target, "v2")
    assert target.read_text() == "v2"
    # No temporary files left behind.
    assert list(tmp_path.iterdir()) == [target]


def test_failure_before_replace_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f.txt"
    atomic_write_text(target, "v1")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"v2")

    # Original intact and the temp file cleaned up.
    assert target.read_bytes() == b"v1"
    assert list(tmp_path.iterdir()) == [target]
