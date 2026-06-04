"""Tests for polygon scanline geometry used by bubble-shape fitting (SG-6)."""

from __future__ import annotations

from mfo.core.geometry import Point
from mfo.render.shape import band_inner, scanline_span


def _diamond(cx: float, cy: float, r: float) -> list[Point]:
    """A diamond (rotated square): widest at its vertical centre, a point at top/bottom."""
    return [
        Point(x=cx, y=cy - r),
        Point(x=cx + r, y=cy),
        Point(x=cx, y=cy + r),
        Point(x=cx - r, y=cy),
    ]


def test_scanline_span_widest_at_centre() -> None:
    diamond = _diamond(50, 50, 40)
    # At the vertical centre the diamond spans the full width...
    mid = scanline_span(diamond, 50)
    assert mid is not None and round(mid[1] - mid[0]) == 80
    # ...and near the top it is much narrower.
    near_top = scanline_span(diamond, 20)
    assert near_top is not None
    assert (near_top[1] - near_top[0]) < (mid[1] - mid[0])


def test_scanline_span_outside_returns_none() -> None:
    diamond = _diamond(50, 50, 40)
    assert scanline_span(diamond, -5) is None  # above the shape
    assert scanline_span(diamond, 200) is None  # below the shape


def test_band_inner_is_narrower_than_a_single_scanline() -> None:
    diamond = _diamond(50, 50, 40)
    # A band straddling the centre is limited by its narrower (upper) edge, not the centre width.
    band = band_inner(diamond, 30, 50)
    centre = scanline_span(diamond, 50)
    assert band is not None and centre is not None
    assert (band[1] - band[0]) < (centre[1] - centre[0])


def test_band_inner_none_when_band_leaves_polygon() -> None:
    diamond = _diamond(50, 50, 40)
    assert band_inner(diamond, 45, 95) is None  # bottom of the band is past the lower vertex


def test_scanline_span_degenerate_polygon_is_none() -> None:
    # Fewer than three vertices can't enclose an interior.
    assert scanline_span([Point(x=0, y=0), Point(x=10, y=0)], 0) is None


def test_scanline_span_ignores_horizontal_edges() -> None:
    # A square has horizontal top/bottom edges; a mid scanline still reports the full width.
    square = [Point(x=0, y=0), Point(x=10, y=0), Point(x=10, y=10), Point(x=0, y=10)]
    span = scanline_span(square, 5)
    assert span == (0.0, 10.0)


def test_band_inner_rejects_inverted_or_undersampled_band() -> None:
    diamond = _diamond(50, 50, 40)
    assert band_inner(diamond, 60, 40) is None  # y_bottom < y_top
    assert band_inner(diamond, 40, 60, samples=1) is None  # need at least two scanlines


def test_band_inner_none_when_interior_collapses() -> None:
    diamond = _diamond(50, 50, 40)
    # A band pinned at the very bottom vertex has no positive-width interior.
    assert band_inner(diamond, 89, 90) is None
