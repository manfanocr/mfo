"""Group regions into conversation chains from their geometry (spec §10.5; FR-19, G-3; MVP-5).

Pure logic, no I/O: given a page's regions (already carrying a ``reading_order_index`` from
:mod:`mfo.core.reading_order`), partition them into ordered chains that should be treated as one
logical text unit. The heuristic is deliberately conservative — it merges *consecutive*, *same-type*
regions that sit close together (a single utterance split across stacked bubbles, or a bubble and
its tail), and leaves distinct utterances as separate units. Each chain becomes one
:class:`~mfo.core.models.TranslationUnit`, which carries a single selected translation; broader
*conversation* context across units is layered in later via the translation context bundle (M4),
not by lumping a whole exchange into one unit.

Like reading order, grouping is pure geometry + region type, so it needs no imaging or OCR
dependency and runs on the fully offline core path. It is the seam the review editor (M6) reuses
when a user merges or splits regions.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from mfo.core.enums import RegionType
from mfo.core.geometry import BBox
from mfo.core.models import Region

# Two regions chain when the empty space between them is at most this fraction of their mean
# height — small enough to keep separate utterances apart, large enough to rejoin a split bubble.
DEFAULT_GAP_RATIO = 0.4

# Region types that never chain: SFX is rarely part of dialogue, so each stays its own unit.
_NON_CHAINING = frozenset({RegionType.SFX})


def _gap_ratio(a: BBox, b: BBox) -> float:
    """Edge-to-edge separation between two boxes, normalized by their mean height.

    Overlapping boxes have a gap of 0; the result grows with the empty space between them.
    """
    dx = max(0.0, max(a.x, b.x) - min(a.right, b.right))
    dy = max(0.0, max(a.y, b.y) - min(a.bottom, b.bottom))
    gap = math.hypot(dx, dy)
    scale = (a.height + b.height) / 2
    return gap / scale if scale > 0 else math.inf


def _chainable(a: Region, b: Region, *, max_gap_ratio: float) -> bool:
    """Whether ``b`` continues the same utterance as the preceding region ``a``."""
    if a.type != b.type or a.type in _NON_CHAINING:
        return False
    return _gap_ratio(a.bbox, b.bbox) <= max_gap_ratio


def _ordering_key(region: Region) -> tuple[float, float, float]:
    # Reading order drives chaining; regions without an index sort last but stay deterministic.
    index = region.reading_order_index
    rank = float(index) if index is not None else math.inf
    return (rank, region.bbox.y, region.bbox.x)


def group_regions(
    regions: Iterable[Region], *, max_gap_ratio: float = DEFAULT_GAP_RATIO
) -> list[list[Region]]:
    """Partition ``regions`` into ordered chains (without mutating them).

    Regions are walked in reading order; each region either continues the current chain (when it is
    a close, same-type neighbour of the previous region) or starts a new one. Returns a list of
    chains, each a list of regions in reading order.
    """
    ordered = sorted(regions, key=_ordering_key)
    if not ordered:
        return []

    chains: list[list[Region]] = [[ordered[0]]]
    for region in ordered[1:]:
        previous = chains[-1][-1]
        if _chainable(previous, region, max_gap_ratio=max_gap_ratio):
            chains[-1].append(region)
        else:
            chains.append([region])
    return chains
