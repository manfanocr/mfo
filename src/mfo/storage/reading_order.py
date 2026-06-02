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

from mfo.core import Page, Region
from mfo.core.enums import ReadingDirection
from mfo.core.reading_order import order_regions
from mfo.storage.hashing import content_key
from mfo.storage.project import ProjectStore


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
    force: bool = False,
) -> list[Region]:
    """Assign each region a ``reading_order_index`` per page; returns the regions reordered."""
    updated: list[Region] = []
    for page in store.db.list(Page, order_by="idx"):
        regions = store.db.list(Region, where=("page_id", page.id))
        if not regions:
            continue

        page_signature = content_key(
            f"reading-order|{direction.value}", _regions_fingerprint(regions)
        )
        if not force and page.structure.get("signature") == page_signature:
            continue

        ordered = order_regions(regions, direction=direction)
        for index, region in enumerate(ordered):
            reordered = region.model_copy(update={"reading_order_index": index})
            store.db.save(reordered)
            updated.append(reordered)
        new_page = page.model_copy(
            update={
                "structure": {
                    "signature": page_signature,
                    "direction": direction.value,
                    "count": len(ordered),
                }
            }
        )
        store.db.save(new_page)
    return updated
