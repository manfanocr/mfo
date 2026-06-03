"""Infer manga reading order from region geometry (spec §10.5; FR-16, FR-17; MVP-5).

Pure geometry, no I/O: given a page's regions and a reading direction, return them in reading
order so each can be assigned a ``reading_order_index``. The heuristic is tier-aware — a manga page
is read one horizontal tier at a time, top to bottom, and within a tier along the reading direction
(right-to-left for RTL, left-to-right for LTR). That orders the common multi-panel grid correctly
where a naive raster scan would not. Tall panels spanning tiers are the case this flat scan still
gets wrong; :func:`order_regions_by_panels` refines it when panel boxes are available (best-effort —
FR-18). ``order_regions`` is the seam the review editor (M6) reuses and that manual correction
(FR-20) ultimately overrides.
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


def _order_indices(boxes: list[BBox], *, direction: ReadingDirection) -> list[int]:
    """Return indices into ``boxes`` in tier-aware reading order.

    Boxes are grouped into tiers by vertical overlap; tiers are read top-to-bottom and each tier is
    swept along the reading direction (rightmost first for RTL, leftmost first for LTR). Ties break
    by vertical position so the order is deterministic. Shared by region and panel ordering.
    """
    rtl = direction is ReadingDirection.RTL
    # Visit boxes top-down first so tiers are created in top-to-bottom order.
    top_down = sorted(range(len(boxes)), key=lambda i: (boxes[i].y, boxes[i].x))

    tiers: list[list[int]] = []
    anchors: list[BBox] = []
    for i in top_down:
        for tier_index, anchor in enumerate(anchors):
            if _y_overlap_ratio(boxes[i], anchor) >= ROW_OVERLAP:
                tiers[tier_index].append(i)
                break
        else:
            tiers.append([i])
            anchors.append(boxes[i])

    def along_tier(i: int) -> tuple[float, float]:
        center = boxes[i].x + boxes[i].width / 2
        return (-center if rtl else center, boxes[i].y)

    ordered: list[int] = []
    for tier in tiers:
        ordered.extend(sorted(tier, key=along_tier))
    return ordered


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
    order = _order_indices([r.bbox for r in items], direction=direction)
    return [items[i] for i in order]


def _panel_of(box: BBox, panels: list[BBox]) -> int | None:
    """Index of the panel best containing ``box`` (by overlap area), or ``None`` if it sits outside.

    A region is assigned to whichever panel its area overlaps most; a region overlapping no panel
    (e.g. art-bleed SFX outside every frame) returns ``None`` so the caller can place it separately.
    """
    best_index: int | None = None
    best_overlap = 0.0
    for index, panel in enumerate(panels):
        inter_w = max(0.0, min(box.right, panel.right) - max(box.x, panel.x))
        inter_h = max(0.0, min(box.bottom, panel.bottom) - max(box.y, panel.y))
        overlap = inter_w * inter_h
        if overlap > best_overlap:
            best_overlap = overlap
            best_index = index
    return best_index


def order_regions_by_panels(
    regions: Iterable[Region],
    panels: Iterable[BBox],
    *,
    direction: ReadingDirection = ReadingDirection.RTL,
) -> list[Region]:
    """Return ``regions`` in panel-aware reading order (without mutating them).

    Panels are read in reading order; within each panel its regions are read in reading order; the
    panels' streams are then concatenated. This refines the flat heuristic on layouts where a single
    page-wide tier scan misorders frames — e.g. a tall panel spanning several tiers beside a stack
    of shorter ones. Regions outside every panel are appended last in plain reading order, and with
    no panels this degrades exactly to :func:`order_regions` (best-effort — FR-18).
    """
    items = list(regions)
    if not items:
        return []
    panel_boxes = list(panels)
    if not panel_boxes:
        return order_regions(items, direction=direction)

    buckets: list[list[Region]] = [[] for _ in panel_boxes]
    outside: list[Region] = []
    for region in items:
        index = _panel_of(region.bbox, panel_boxes)
        (outside if index is None else buckets[index]).append(region)

    ordered: list[Region] = []
    for panel_index in _order_indices(panel_boxes, direction=direction):
        ordered.extend(order_regions(buckets[panel_index], direction=direction))
    ordered.extend(order_regions(outside, direction=direction))
    return ordered
