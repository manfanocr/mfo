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
from mfo.core.enums import RegionStatus
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
    reuse_detection: bool = True,
    force: bool = False,
) -> list[OCRSpan]:
    """OCR every region on every page, persisting spans + a per-page signature. Returns new ones.

    When ``reuse_detection`` and a page was recognized by a det+rec detector (it carries provisional
    spans stamped with the detector's id, batch 8.0), those spans are **adopted** instead of running
    ``recognize`` again — only regions without detection text are recognized. Passing
    ``reuse_detection=False`` (or ``force``) ignores them and recognizes everything with the given
    engine, so an explicit OCR engine stays authoritative. The returned list is the spans newly
    produced by ``recognize`` this run (adopted detection spans are not "new").
    """
    created: list[OCRSpan] = []
    for page in store.db.list(Page, order_by="idx"):
        regions = store.db.list(Region, where=("page_id", page.id))
        if not regions:
            continue
        # Regions auto-marked IGNORE (panel/frame blobs) are not real text; skip OCR for them.
        eligible = [region for region in regions if region.status is not RegionStatus.IGNORE]

        # Adopt detection-provided OCR only when asked, the detector recognized text, and we know
        # which detector's spans to trust (so a prior OCR-stage run is never mistaken for it).
        detector_id = page.detection.get("detector")
        adopt = reuse_detection and bool(page.detection.get("recognized")) and bool(detector_id)

        original = store.layout.root / page.image_path
        source_hash = sha256_file(original)
        page_signature = content_key(
            source_hash, f"{signature}|reuse={adopt}|{_regions_fingerprint(regions)}"
        )

        current = page.ocr
        existing = [
            span
            for region in eligible
            for span in store.db.list(OCRSpan, where=("region_id", region.id))
        ]
        if (
            not force
            and current.get("signature") == page_signature
            and len(existing) == len(eligible)
        ):
            continue

        # Recompute (forced or stale): clear spans on ignored regions outright, and on eligible
        # regions clear everything except a detection span we're adopting, so none are orphaned.
        for region in regions:
            if region.status is RegionStatus.IGNORE:
                for span in store.db.list(OCRSpan, where=("region_id", region.id)):
                    store.db.delete(OCRSpan, where=("id", span.id))

        new_spans: list[OCRSpan] = []
        reused = 0
        for region in eligible:
            region_spans = store.db.list(OCRSpan, where=("region_id", region.id))
            adopted = (
                next((s for s in region_spans if s.source == detector_id), None) if adopt else None
            )
            for span in region_spans:
                if adopted is None or span.id != adopted.id:
                    store.db.delete(OCRSpan, where=("id", span.id))
            if adopted is not None:
                reused += 1
                continue
            result = recognize(original, region.bbox)
            span = OCRSpan(
                region_id=region.id,
                text=result.text,
                confidence=result.confidence,
                alternatives=list(result.alternatives),
                source=signature,
            )
            store.db.save(span)
            new_spans.append(span)

        new_page = page.model_copy(
            update={
                "ocr": {
                    "signature": page_signature,
                    "engine": signature,
                    "count": len(eligible),
                    "reused": reused,
                }
            }
        )
        store.db.save(new_page)
        created.extend(new_spans)
    return created
