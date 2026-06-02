"""Persist dialogue grouping per page (spec §10.5; FR-11, FR-19; G-3; I-2, I-3, NFR-8).

Grouping is pure structure over a page's regions (see :mod:`mfo.core.grouping`) — geometry plus
region type and reading order — so, like reading order, it needs no imaging or OCR dependency and
runs on the fully offline core path. Each page records a grouping signature folding its regions'
geometry/type/order and the grouping parameters; re-running skips unchanged pages (NFR-8), and a
re-detection or re-ordering (which changes the fingerprint) invalidates it.

Each chain becomes one :class:`~mfo.core.models.TranslationUnit` carrying the ordered region IDs of
its members, establishing the page → unit → region link graph (I-2). The unit's ``source_bundle``
is left empty here: assembling source text from OCR belongs to the translation/context stage (M4),
keeping grouping independent of OCR. Recomputing a page drops its prior units first, so grouping is
idempotent and a forced recompute never leaves stale units behind. Because the skip is
signature-driven, a re-run with unchanged structure leaves existing units (and any translations they
later carry) untouched — automation never silently discards them (I-3); they are only rebuilt on a
real input change or an explicit ``force``.
"""

from __future__ import annotations

import hashlib

from mfo.core import Page, Region, TranslationUnit
from mfo.core.grouping import DEFAULT_GAP_RATIO, group_regions
from mfo.storage.hashing import content_key
from mfo.storage.project import ProjectStore


def _regions_fingerprint(regions: list[Region]) -> str:
    """A stable digest of a page's regions for grouping, sensitive to type and reading order.

    Sorted by id so the digest is independent of row order; folds in type and reading-order index
    because grouping depends on both — a re-detection or re-ordering must invalidate the grouping.
    """
    digest = hashlib.sha256()
    for region in sorted(regions, key=lambda r: r.id):
        b = region.bbox
        digest.update(
            f"{region.id}:{b.x},{b.y},{b.width},{b.height}:"
            f"{region.type.value}:{region.reading_order_index}\n".encode()
        )
    return digest.hexdigest()


def group_into_units(
    store: ProjectStore,
    *,
    max_gap_ratio: float | None = None,
    force: bool = False,
) -> list[TranslationUnit]:
    """Group each page's regions into translation units; returns the units created."""
    ratio = DEFAULT_GAP_RATIO if max_gap_ratio is None else max_gap_ratio
    created: list[TranslationUnit] = []
    for page in store.db.list(Page, order_by="idx"):
        regions = store.db.list(Region, where=("page_id", page.id))
        if not regions:
            continue

        page_signature = content_key(f"grouping|{ratio}", _regions_fingerprint(regions))
        if not force and page.grouping.get("signature") == page_signature:
            continue

        chains = group_regions(regions, max_gap_ratio=ratio)
        units = [
            TranslationUnit(
                page_id=page.id,
                ordered_region_ids=[region.id for region in chain],
            )
            for chain in chains
        ]

        # Recompute (forced or stale): drop any prior units so none are orphaned.
        store.db.delete(TranslationUnit, where=("page_id", page.id))
        store.db.save_all(units)
        new_page = page.model_copy(
            update={
                "grouping": {
                    "signature": page_signature,
                    "max_gap_ratio": ratio,
                    "count": len(units),
                }
            }
        )
        store.db.save(new_page)
        created.extend(units)
    return created
