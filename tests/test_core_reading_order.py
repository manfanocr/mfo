"""Tests for the pure reading-order heuristic (spec §10.5; FR-16, FR-17; MVP-5)."""

from __future__ import annotations

from mfo.core.enums import ReadingDirection
from mfo.core.geometry import BBox
from mfo.core.models import Region
from mfo.core.reading_order import order_regions, order_regions_by_panels


def _region(name: str, x: float, y: float, w: float = 40, h: float = 40) -> Region:
    # The id carries the label so assertions read cleanly.
    return Region(id=f"rgn_{name}", page_id="pg", bbox=BBox(x=x, y=y, width=w, height=h))


def _labels(regions: list[Region]) -> list[str]:
    return [r.id.removeprefix("rgn_") for r in regions]


def test_empty_input() -> None:
    assert order_regions([]) == []


def test_rtl_grid_reads_tiers_top_down_right_to_left() -> None:
    # A 2x2 panel grid. Manga (RTL) reads the top tier right→left, then the bottom tier.
    a = _region("A", x=0, y=0)  # top-left
    b = _region("B", x=60, y=0)  # top-right
    c = _region("C", x=0, y=60)  # bottom-left
    d = _region("D", x=60, y=60)  # bottom-right

    order = order_regions([a, c, d, b], direction=ReadingDirection.RTL)
    assert _labels(order) == ["B", "A", "D", "C"]


def test_ltr_grid_reads_tiers_top_down_left_to_right() -> None:
    a = _region("A", x=0, y=0)
    b = _region("B", x=60, y=0)
    c = _region("C", x=0, y=60)
    d = _region("D", x=60, y=60)

    order = order_regions([d, b, a, c], direction=ReadingDirection.LTR)
    assert _labels(order) == ["A", "B", "C", "D"]


def test_default_direction_is_rtl() -> None:
    left = _region("L", x=0, y=0)
    right = _region("R", x=60, y=0)
    assert _labels(order_regions([left, right])) == ["R", "L"]


def test_regions_in_the_same_tier_group_despite_misaligned_tops() -> None:
    # Two bubbles in one tier whose tops differ slightly still read as one tier, right→left.
    right = _region("R", x=60, y=0, h=40)
    left = _region("L", x=0, y=8, h=40)  # mostly overlaps R vertically
    below = _region("B", x=30, y=80, h=40)

    assert _labels(order_regions([left, below, right])) == ["R", "L", "B"]


def test_does_not_mutate_input_regions() -> None:
    a = _region("A", x=0, y=0)
    b = _region("B", x=60, y=0)
    order_regions([a, b])
    assert a.reading_order_index is None and b.reading_order_index is None


# -- Panel-aware ordering (batch 3.3; FR-18) ----------------------------------------------------

# The tricky layout the flat scan misorders: a full-height panel on the left beside two stacked
# panels on the right. RTL reads the right column top→bottom, then the left panel.
_LEFT_TALL = BBox(x=10, y=10, width=80, height=180)
_RIGHT_TOP = BBox(x=110, y=10, width=80, height=80)
_RIGHT_BOTTOM = BBox(x=110, y=110, width=80, height=80)


def test_flat_order_misorders_the_tall_panel_layout() -> None:
    # Establishes the baseline failure that panel-awareness fixes (DoD 3.3).
    rt = _region("rt", x=140, y=40, w=20, h=20)
    mid = _region("l", x=40, y=90, w=20, h=20)  # inside the tall left panel
    rb = _region("rb", x=140, y=140, w=20, h=20)
    # A flat top-down scan slots the left region between the two right ones.
    assert _labels(order_regions([rt, mid, rb])) == ["rt", "l", "rb"]


def test_panel_aware_order_reads_each_panel_in_turn() -> None:
    rt = _region("rt", x=140, y=40, w=20, h=20)
    mid = _region("l", x=40, y=90, w=20, h=20)
    rb = _region("rb", x=140, y=140, w=20, h=20)
    panels = [_LEFT_TALL, _RIGHT_TOP, _RIGHT_BOTTOM]

    ordered = order_regions_by_panels([rt, mid, rb], panels, direction=ReadingDirection.RTL)
    # Right column first (top→bottom), then the left panel — the human reading order.
    assert _labels(ordered) == ["rt", "rb", "l"]


def test_panel_aware_order_groups_multiple_regions_within_a_panel() -> None:
    # Two bubbles in the right-top panel read right→left before moving on.
    rt_right = _region("rt_r", x=160, y=30, w=20, h=20)
    rt_left = _region("rt_l", x=120, y=30, w=20, h=20)
    rb = _region("rb", x=140, y=140, w=20, h=20)
    panels = [_RIGHT_TOP, _RIGHT_BOTTOM]

    ordered = order_regions_by_panels([rt_left, rb, rt_right], panels)
    assert _labels(ordered) == ["rt_r", "rt_l", "rb"]


def test_regions_outside_every_panel_are_appended_last() -> None:
    inside = _region("in", x=140, y=40, w=20, h=20)
    stray = _region("out", x=10, y=300, w=20, h=20)  # below all panels, in no frame
    ordered = order_regions_by_panels([stray, inside], [_RIGHT_TOP])
    assert _labels(ordered) == ["in", "out"]


def test_panel_aware_falls_back_to_flat_without_panels() -> None:
    left = _region("L", x=0, y=0)
    right = _region("R", x=60, y=0)
    assert _labels(order_regions_by_panels([left, right], [])) == ["R", "L"]


def test_panel_aware_empty_input() -> None:
    assert order_regions_by_panels([], [_RIGHT_TOP]) == []
