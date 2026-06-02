"""Persist OCR output per region (spec §10.4; FR-6, FR-12, FR-13, FR-15; I-2, NFR-8).

The recognition callable is *injected* (the vision layer supplies it) so storage stays free of
any imaging dependency, mirroring the detect/preprocess stages. OCR runs on the regions a page
already has; each page records an OCR signature folding the source image, the engine id, and a
fingerprint of its regions — so re-running skips unchanged pages (NFR-8) and a re-detection
(which changes the regions) correctly invalidates the OCR. When a page is (re)OCR'd its prior
``OCRSpan`` rows are cleared first, so OCR is idempotent and a forced recompute never leaves
stale spans behind. OCR is stored separately from translation (FR-15).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from mfo.core import OCRSpan, Page, Region
from mfo.core.geometry import BBox
from mfo.storage.hashing import content_key, sha256_file
from mfo.storage.project import ProjectStore


class RecognizedSpan(Protocol):
    """The minimum a recognition result must expose to be persisted."""

    @property
    def text(self) -> str: ...

    @property
    def confidence(self) -> float | None: ...

    @property
    def alternatives(self) -> list[str]: ...


Recognize = Callable[[Path, BBox], RecognizedSpan]


def _regions_fingerprint(regions: list[Region]) -> str:
    """A stable digest of a page's regions, so re-detection invalidates that page's OCR."""
    digest = hashlib.sha256()
    for region in regions:
        b = region.bbox
        digest.update(f"{region.id}:{b.x},{b.y},{b.width},{b.height}\n".encode())
    return digest.hexdigest()


def ocr_regions(
    store: ProjectStore,
    *,
    recognize: Recognize,
    signature: str,
    force: bool = False,
) -> list[OCRSpan]:
    """OCR every region on every page, persisting spans + a per-page signature. Returns new ones."""
    created: list[OCRSpan] = []
    for page in store.db.list(Page, order_by="idx"):
        regions = store.db.list(Region, where=("page_id", page.id))
        if not regions:
            continue

        original = store.layout.root / page.image_path
        source_hash = sha256_file(original)
        page_signature = content_key(source_hash, f"{signature}|{_regions_fingerprint(regions)}")

        current = page.ocr
        existing = [
            span
            for region in regions
            for span in store.db.list(OCRSpan, where=("region_id", region.id))
        ]
        if (
            not force
            and current.get("signature") == page_signature
            and len(existing) == len(regions)
        ):
            continue

        # Recompute (forced or stale): drop any prior spans so none are orphaned.
        for region in regions:
            store.db.delete(OCRSpan, where=("region_id", region.id))
        spans = [
            OCRSpan(
                region_id=region.id,
                text=result.text,
                confidence=result.confidence,
                alternatives=list(result.alternatives),
            )
            for region in regions
            for result in (recognize(original, region.bbox),)
        ]
        store.db.save_all(spans)
        new_page = page.model_copy(
            update={
                "ocr": {
                    "signature": page_signature,
                    "engine": signature,
                    "count": len(spans),
                }
            }
        )
        store.db.save(new_page)
        created.extend(spans)
    return created
