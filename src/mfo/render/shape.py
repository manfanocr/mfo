"""Polygon scanline geometry for bubble-shape-aware text fitting (§10.8; SG-6; FR-34, NFR-3).

Pure and dependency-light. A speech bubble is rarely a rectangle: a round or oval bubble is widest
across its middle and narrows toward the top and bottom. To keep text from spilling over the outline
(SG-6), the typesetter needs to know how wide the bubble's *interior* is at each line of text. These
helpers answer that from the region's polygon (the ``Region.polygon`` field): :func:`scanline_span`
gives the interior horizontal span at one height, and :func:`band_inner` gives the span guaranteed
to sit inside the polygon across a whole line's vertical band.

The math assumes a simple, roughly-convex bubble outline (what detectors emit); for a convex polygon
the band span is exact, and for a mildly concave one it is a safe over-estimate of height but the
band's min/max keeps each line inside the hull, which is the conservative thing to do.
"""

from __future__ import annotations

from collections.abc import Sequence

from mfo.core.geometry import Point


def scanline_span(polygon: Sequence[Point], y: float) -> tuple[float, float] | None:
    """The interior horizontal span ``(left, right)`` of ``polygon`` at height ``y``, or ``None``.

    Intersects every edge with the horizontal line ``y`` (half-open in ``y`` so a shared vertex is
    counted once) and returns the outermost pair of crossings — the interior for a convex outline.
    ``None`` when the line misses the polygon (fewer than two crossings).
    """
    n = len(polygon)
    if n < 3:
        return None
    xs: list[float] = []
    for i in range(n):
        a = polygon[i]
        b = polygon[(i + 1) % n]
        y0, y1 = a.y, b.y
        if y0 == y1:
            continue  # horizontal edge contributes no crossing
        lo, hi = (y0, y1) if y0 < y1 else (y1, y0)
        if lo <= y < hi:  # half-open avoids double-counting a vertex shared by two edges
            t = (y - y0) / (y1 - y0)
            xs.append(a.x + t * (b.x - a.x))
    if len(xs) < 2:
        return None
    return min(xs), max(xs)


def band_inner(
    polygon: Sequence[Point], y_top: float, y_bottom: float, *, samples: int = 5
) -> tuple[float, float] | None:
    """The widest ``(left, right)`` span that stays inside ``polygon`` across ``[y_top, y_bottom]``.

    Samples a few scanlines across the band and intersects their spans (max of lefts, min rights),
    so a line drawn within the result never crosses the outline anywhere in its vertical extent.
    Returns ``None`` if any sampled scanline falls outside the polygon or the band has no interior.
    """
    if y_bottom < y_top or samples < 2:
        return None
    left = float("-inf")
    right = float("inf")
    for i in range(samples):
        y = y_top + (y_bottom - y_top) * i / (samples - 1)
        span = scanline_span(polygon, y)
        if span is None:
            return None
        left = max(left, span[0])
        right = min(right, span[1])
    if right <= left:
        return None
    return left, right
