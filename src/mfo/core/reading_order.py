"""Infer manga reading order from region geometry (spec §10.5; FR-16, FR-17; MVP-5).

Pure geometry, no I/O: given a page's regions and a reading direction, return them in reading
order so each can be assigned a ``reading_order_index``. The heuristic is tier-aware — a manga page
is read one horizontal tier at a time, top to bottom, and within a tier along the reading direction
(right-to-left for RTL, left-to-right for LTR). That orders the common multi-panel grid correctly
where a naive raster scan would not. Tall panels spanning tiers are a known hard case left to panel
detection (batch 3.3). ``order_regions`` is the seam the review editor (M6) reuses and that manual
correction (FR-20) ultimately overrides.
"""

from __future__ import annotations

from collections.abc import Iterable

from mfo.core.enums import ReadingDirection
from mfo.core.geometry import BBox
from mfo.core.models import Region

# Two regions share a tier when their vertical spans overlap by at least this fraction of the
# shorter one — high enough to keep adjacent tiers apart, low enough to group bubbles whose tops
# are not perfectly aligned.
ROW_OVERLAP = 0.5


def _y_overlap_ratio(a: BBox, b: BBox) -> float:
    overlap = max(0.0, min(a.bottom, b.bottom) - max(a.y, b.y))
    shorter = min(a.height, b.height)
    return overlap / shorter if shorter > 0 else 0.0


def order_regions(
    regions: Iterable[Region], *, direction: ReadingDirection = ReadingDirection.RTL
) -> list[Region]:
    """Return ``regions`` in reading order (without mutating them).

    Regions are grouped into tiers by vertical overlap; tiers are read top-to-bottom and each tier
    is swept along the reading direction (rightmost first for RTL, leftmost first for LTR). Ties
    break by vertical position so the order is deterministic.
    """
    items = list(regions)
    if not items:
        return []

    rtl = direction is ReadingDirection.RTL
    # Visit regions top-down first so tiers are created in top-to-bottom order.
    top_down = sorted(items, key=lambda r: (r.bbox.y, r.bbox.x))

    tiers: list[list[Region]] = []
    anchors: list[BBox] = []
    for region in top_down:
        for index, anchor in enumerate(anchors):
            if _y_overlap_ratio(region.bbox, anchor) >= ROW_OVERLAP:
                tiers[index].append(region)
                break
        else:
            tiers.append([region])
            anchors.append(region.bbox)

    def along_tier(region: Region) -> tuple[float, float]:
        center = region.bbox.x + region.bbox.width / 2
        return (-center if rtl else center, region.bbox.y)

    ordered: list[Region] = []
    for tier in tiers:
        ordered.extend(sorted(tier, key=along_tier))
    return ordered
