"""Tests for the pure reading-order heuristic (spec §10.5; FR-16, FR-17; MVP-5)."""

from __future__ import annotations

from mfo.core.enums import ReadingDirection
from mfo.core.geometry import BBox
from mfo.core.models import Region
from mfo.core.reading_order import order_regions


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
