"""Tests for content hashing and the content-addressed cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from mfo.storage.cache import Cache
from mfo.storage.hashing import content_key, sha256_bytes, sha256_file


def test_content_key_is_stable_and_distinct() -> None:
    assert content_key("a", "b") == content_key("a", "b")
    assert content_key("a") != content_key("b")
    # Length-prefixing prevents ("a","b") colliding with ("ab","").
    assert content_key("a", "b") != content_key("ab", "")


def test_sha256_file_matches_bytes(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"hello")
    assert sha256_file(path) == sha256_bytes(b"hello")


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    key = content_key("stage", "input-v1")
    assert not cache.has(key)
    assert cache.get(key) is None

    cache.write_bytes(key, b"payload")
    assert cache.has(key)
    assert cache.read_bytes(key) == b"payload"
    assert cache.get(key) == b"payload"
    # Sharded under the key prefix.
    assert cache.path_for(key).parent.name == key[:2]


def test_cache_rejects_short_key(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    with pytest.raises(ValueError):
        cache.path_for("a")
