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
    first = build_context(sources, 0, page_index=0, page_count=3, window=1)
    last = build_context(sources, 2, page_index=0, page_count=3, window=1)
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


def test_default_window_spans_two_neighbours() -> None:
    # With one unit per bubble, the default window carries two lines of context each side (FR-22).
    sources = ["a", "b", "c", "d", "e"]
    assert DEFAULT_NEIGHBOR_WINDOW == 2
    bundle = build_context(sources, 2, page_index=0, page_count=1)
    assert bundle["preceding"] == ["a", "b"]
    assert bundle["following"] == ["d", "e"]


# -- Panel-aware context (batch 8.4; SG-1, FR-18, FR-22) ----------------------------------------


def test_panel_scopes_neighbours_to_the_same_panel() -> None:
    # Two panels: [a,b,c] then [d,e]. c (panel 0) must not see d/e (panel 1) across the boundary.
    sources = ["a", "b", "c", "d", "e"]
    panels = [0, 0, 0, 1, 1]
    bundle = build_context(sources, 2, page_index=0, page_count=1, window=2, panels=panels)
    assert bundle["panel"] == 0
    assert bundle["preceding"] == ["a", "b"]
    assert bundle["following"] == []  # would be d, e without panel scoping — that bleed is gone


def test_panel_neighbours_on_the_other_panel() -> None:
    sources = ["a", "b", "c", "d", "e"]
    panels = [0, 0, 0, 1, 1]
    bundle = build_context(sources, 3, page_index=0, page_count=1, window=2, panels=panels)
    assert bundle["panel"] == 1
    assert bundle["preceding"] == []  # c is in panel 0, so it is not pulled in
    assert bundle["following"] == ["e"]


def test_without_panels_context_bleeds_across_the_boundary() -> None:
    # The A/B contrast: same sources, no panel data → the flat window crosses the frame boundary.
    sources = ["a", "b", "c", "d", "e"]
    bundle = build_context(sources, 2, page_index=0, page_count=1, window=2)
    assert "panel" not in bundle
    assert bundle["following"] == ["d", "e"]


def test_out_of_panel_unit_keeps_the_plain_window() -> None:
    # A region outside every panel (None) has no frame to scope to → plain reading-order window.
    sources = ["a", "b", "c", "d", "e"]
    panels = [0, 0, None, 1, 1]
    bundle = build_context(sources, 2, page_index=0, page_count=1, window=2, panels=panels)
    assert bundle["panel"] is None
    assert bundle["preceding"] == ["a", "b"]
    assert bundle["following"] == ["d", "e"]


def test_panel_window_still_drops_empty_neighbours() -> None:
    sources = ["a", "", "c", "d"]
    panels = [0, 0, 0, 0]
    bundle = build_context(sources, 2, page_index=0, page_count=1, window=2, panels=panels)
    assert bundle["preceding"] == ["a"]  # the empty predecessor is omitted
    assert bundle["following"] == ["d"]
