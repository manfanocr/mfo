"""Tests for the pure translation context builder (§10.6, §12.5; FR-22, NFR-2)."""

from __future__ import annotations

from mfo.core.context import DEFAULT_NEIGHBOR_WINDOW, build_context


def test_middle_unit_sees_neighbours_both_sides() -> None:
    sources = ["a", "b", "c"]
    bundle = build_context(sources, 1, page_index=0, page_count=3)
    assert bundle["preceding"] == ["a"]
    assert bundle["following"] == ["c"]


def test_first_and_last_unit_have_one_sided_context() -> None:
    sources = ["a", "b", "c"]
    first = build_context(sources, 0, page_index=0, page_count=3)
    last = build_context(sources, 2, page_index=0, page_count=3)
    assert first["preceding"] == [] and first["following"] == ["b"]
    assert last["preceding"] == ["b"] and last["following"] == []


def test_window_widens_the_neighbourhood() -> None:
    sources = ["a", "b", "c", "d", "e"]
    bundle = build_context(sources, 2, page_index=0, page_count=1, window=2)
    assert bundle["preceding"] == ["a", "b"]
    assert bundle["following"] == ["d", "e"]


def test_empty_neighbours_are_dropped() -> None:
    sources = ["", "b", ""]
    bundle = build_context(sources, 1, page_index=0, page_count=1)
    assert bundle["preceding"] == []  # the empty predecessor is omitted
    assert bundle["following"] == []


def test_page_locator_is_carried() -> None:
    bundle = build_context(["only"], 0, page_index=4, page_count=12)
    assert bundle["page_index"] == 4
    assert bundle["page_count"] == 12


def test_default_window_is_immediate_neighbours() -> None:
    sources = ["a", "b", "c", "d"]
    assert DEFAULT_NEIGHBOR_WINDOW == 1
    bundle = build_context(sources, 2, page_index=0, page_count=1)
    assert bundle["preceding"] == ["b"]  # only the immediate predecessor
    assert bundle["following"] == ["d"]
