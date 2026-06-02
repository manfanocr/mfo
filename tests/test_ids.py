"""Tests for the ULID-based identifier scheme."""

from __future__ import annotations

import pytest

from mfo.core.ids import _ALPHABET, _ULID_LENGTH, new_id, new_ulid


def test_ulid_format() -> None:
    ulid = new_ulid()
    assert len(ulid) == _ULID_LENGTH
    assert all(char in _ALPHABET for char in ulid)


def test_ids_are_unique() -> None:
    ids = {new_ulid() for _ in range(10_000)}
    assert len(ids) == 10_000


def test_ulid_is_time_sortable() -> None:
    earlier = new_ulid(timestamp_ms=1_000)
    later = new_ulid(timestamp_ms=2_000)
    # Lexicographic order matches chronological order.
    assert earlier < later


def test_new_id_is_prefixed_and_self_describing() -> None:
    region_id = new_id("rgn")
    prefix, _, ulid = region_id.partition("_")
    assert prefix == "rgn"
    assert len(ulid) == _ULID_LENGTH


def test_new_id_rejects_empty_prefix() -> None:
    with pytest.raises(ValueError):
        new_id("")


def test_new_ulid_rejects_out_of_range_timestamp() -> None:
    with pytest.raises(ValueError):
        new_ulid(timestamp_ms=-1)
    with pytest.raises(ValueError):
        new_ulid(timestamp_ms=1 << 48)
