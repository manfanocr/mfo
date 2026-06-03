"""Tests for the pure dialogue-grouping heuristic (spec §10.5; FR-19, G-3; MVP-5)."""

from __future__ import annotations

from mfo.core.enums import RegionType
from mfo.core.geometry import BBox
from mfo.core.grouping import group_regions
from mfo.core.models import Region


def _region(
    name: str,
    x: float,
    y: float,
    *,
    w: float = 40,
    h: float = 40,
    type: RegionType = RegionType.BUBBLE,
    order: int | None = None,
) -> Region:
    # The id carries the label so assertions read cleanly.
    return Region(
        id=f"rgn_{name}",
        page_id="pg",
        bbox=BBox(x=x, y=y, width=w, height=h),
        type=type,
        reading_order_index=order,
    )


def _labels(chains: list[list[Region]]) -> list[list[str]]:
    return [[r.id.removeprefix("rgn_") for r in chain] for chain in chains]


def test_empty_input() -> None:
    assert group_regions([]) == []


def test_close_same_type_regions_chain_into_one_unit() -> None:
    # A single utterance split across two near-touching bubbles becomes one chain.
    top = _region("top", x=0, y=0, order=0)
    bottom = _region("bottom", x=0, y=44, order=1)  # 4px gap, well within threshold
    assert _labels(group_regions([top, bottom])) == [["top", "bottom"]]


def test_far_regions_stay_separate() -> None:
    top = _region("top", x=0, y=0, order=0)
    bottom = _region("bottom", x=0, y=100, order=1)  # 60px gap = 1.5x height
    assert _labels(group_regions([top, bottom])) == [["top"], ["bottom"]]


def test_different_types_do_not_chain_even_when_adjacent() -> None:
    bubble = _region("bub", x=0, y=0, order=0)
    caption = _region("cap", x=0, y=44, type=RegionType.CAPTION, order=1)
    assert _labels(group_regions([bubble, caption])) == [["bub"], ["cap"]]


def test_sfx_never_chains() -> None:
    a = _region("a", x=0, y=0, type=RegionType.SFX, order=0)
    b = _region("b", x=0, y=44, type=RegionType.SFX, order=1)
    assert _labels(group_regions([a, b])) == [["a"], ["b"]]


def test_chain_extends_transitively_along_reading_order() -> None:
    a = _region("a", x=0, y=0, order=0)
    b = _region("b", x=0, y=44, order=1)
    c = _region("c", x=0, y=88, order=2)
    assert _labels(group_regions([a, b, c])) == [["a", "b", "c"]]


def test_chains_follow_reading_order_not_input_order() -> None:
    a = _region("a", x=0, y=0, order=0)
    b = _region("b", x=0, y=44, order=1)
    c = _region("c", x=0, y=88, order=2)
    # Shuffled input still chains in reading order.
    assert _labels(group_regions([c, a, b])) == [["a", "b", "c"]]


def test_threshold_is_configurable() -> None:
    top = _region("top", x=0, y=0, order=0)
    bottom = _region("bottom", x=0, y=60, order=1)  # 20px gap = 0.5x mean height

    assert _labels(group_regions([top, bottom])) == [["top"], ["bottom"]]  # default 0.4
    assert _labels(group_regions([top, bottom], max_gap_ratio=0.6)) == [["top", "bottom"]]


def test_zero_ratio_disables_chaining() -> None:
    # max_gap_ratio <= 0 forces one chain per region, even for adjacent same-type bubbles.
    a = _region("a", x=0, y=0, order=0)
    b = _region("b", x=0, y=44, order=1)  # would chain at the default ratio
    assert _labels(group_regions([a, b], max_gap_ratio=0.0)) == [["a"], ["b"]]


def test_does_not_mutate_input_regions() -> None:
    a = _region("a", x=0, y=0, order=0)
    b = _region("b", x=0, y=44, order=1)
    group_regions([a, b])
    assert a.reading_order_index == 0 and b.reading_order_index == 1
