"""Persist reading order per page (spec §10.5; FR-16, FR-17, FR-20; I-2, NFR-8).

Reading order is pure geometry over a page's regions (see :mod:`mfo.core.reading_order`), so —
unlike OCR — it needs no imaging dependency and runs on the fully offline core path. Each page
records a structure signature folding its regions' geometry and the reading direction; re-running
skips unchanged pages (NFR-8), and a re-detection (which changes the regions) invalidates it.
Region IDs are stable across the reassignment (I-2): the index is updated in place, never recreated.

A manually corrected order (FR-20) therefore survives a plain re-run — the signature is unchanged
so the page is skipped, and automation never silently overwrites it (I-3); it is only re-derived
on an explicit ``force``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path

from mfo.core import Page, Region
from mfo.core.enums import ReadingDirection
from mfo.core.geometry import BBox
from mfo.core.reading_order import order_regions, order_regions_by_panels, panel_of
from mfo.storage.hashing import content_key
from mfo.storage.project import ProjectStore

# A page → panel-boxes callable. Injected by the CLI (which wires the vision detector) so storage
# stays free of any imaging dependency, mirroring the detect stage; ``None`` keeps the offline path.
DetectPanels = Callable[[Path], Sequence[BBox]]


def _regions_fingerprint(regions: list[Region]) -> str:
    """A stable digest of a page's regions, so re-detection invalidates its reading order.

    Sorted by id so the digest is independent of row order — reassigning the index resaves the
    rows, which must not perturb the fingerprint and break the idempotent skip.
    """
    digest = hashlib.sha256()
    for region in sorted(regions, key=lambda r: r.id):
        b = region.bbox
        digest.update(f"{region.id}:{b.x},{b.y},{b.width},{b.height}\n".encode())
    return digest.hexdigest()


def assign_reading_order(
    store: ProjectStore,
    *,
    direction: ReadingDirection,
    detect_panels: DetectPanels | None = None,
    force: bool = False,
) -> list[Region]:
    """Assign each region a ``reading_order_index`` per page; returns the regions reordered.

    When ``detect_panels`` is supplied the order is refined panel-by-panel (FR-18): each page's
    image is read (read-only, I-1) to recover its panels, regions are ordered within them, and each
    region is stamped with its ``panel_index`` so the translation context can stay inside the panel
    (SG-1). The panel mode is folded into the signature so toggling it re-runs (NFR-8); with no
    detector the behaviour is the flat, fully-offline heuristic and ``panel_index`` is cleared.
    """
    panel_mode = "panels" if detect_panels is not None else "flat"
    updated: list[Region] = []
    for page in store.db.list(Page, order_by="idx"):
        regions = store.db.list(Region, where=("page_id", page.id))
        if not regions:
            continue

        page_signature = content_key(
            f"reading-order|{direction.value}|{panel_mode}", _regions_fingerprint(regions)
        )
        if not force and page.structure.get("signature") == page_signature:
            continue

        structure: dict[str, object] = {
            "signature": page_signature,
            "direction": direction.value,
            "panels": detect_panels is not None,
        }
        # Stamp each region's panel so the translation context can stay inside it (SG-1, FR-18).
        # ``None`` on the flat path clears any stale panel from a previous panel-mode run.
        panel_by_region: dict[str, int | None] = {region.id: None for region in regions}
        if detect_panels is not None:
            panels = list(detect_panels(store.layout.root / page.image_path))
            ordered = order_regions_by_panels(regions, panels, direction=direction)
            structure["panel_count"] = len(panels)
            panel_by_region = {region.id: panel_of(region.bbox, panels) for region in regions}
        else:
            ordered = order_regions(regions, direction=direction)
        structure["count"] = len(ordered)

        for index, region in enumerate(ordered):
            reordered = region.model_copy(
                update={
                    "reading_order_index": index,
                    "panel_index": panel_by_region[region.id],
                }
            )
            store.db.save(reordered)
            updated.append(reordered)
        store.db.save(page.model_copy(update={"structure": structure}))
    return updated
