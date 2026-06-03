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
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mfo.core import OCRSpan, Page, Region
from mfo.core.enums import RegionStatus
from mfo.core.geometry import BBox
from mfo.core.parallel import parallel_map
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


@dataclass(frozen=True)
class _Job:
    page: Page
    original: Path
    page_signature: str
    regions: list[Region]
    eligible: list[Region]
    recognize_regions: list[Region]  # eligible regions with no adopted detection span
    adopted_span_ids: set[str]
    reused: int


def ocr_regions(
    store: ProjectStore,
    *,
    recognize: Recognize,
    signature: str,
    reuse_detection: bool = True,
    force: bool = False,
    jobs: int = 1,
) -> list[OCRSpan]:
    """OCR every region on every page, persisting spans + a per-page signature. Returns new ones.

    When ``reuse_detection`` and a page was recognized by a det+rec detector (it carries provisional
    spans stamped with the detector's id, batch 8.0), those spans are **adopted** instead of running
    ``recognize`` again — only regions without detection text are recognized. Passing
    ``reuse_detection=False`` (or ``force``) ignores them and recognizes everything with the given
    engine, so an explicit OCR engine stays authoritative. The returned list is the spans newly
    produced by ``recognize`` this run (adopted detection spans are not "new").

    Pages are planned and persisted serially (single SQLite connection, deterministic order); only
    the injected ``recognize`` callable runs concurrently across pages when ``jobs > 1`` — within a
    page its regions are recognized in order (NFR-5/6/7).
    """
    pending: list[_Job] = []
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

        # Decide per eligible region whether a detection span is adopted or the region needs OCR.
        recognize_regions: list[Region] = []
        adopted_span_ids: set[str] = set()
        for region in eligible:
            adopted = (
                next(
                    (
                        s
                        for s in store.db.list(OCRSpan, where=("region_id", region.id))
                        if s.source == detector_id
                    ),
                    None,
                )
                if adopt
                else None
            )
            if adopted is not None:
                adopted_span_ids.add(adopted.id)
            else:
                recognize_regions.append(region)
        pending.append(
            _Job(
                page=page,
                original=original,
                page_signature=page_signature,
                regions=regions,
                eligible=eligible,
                recognize_regions=recognize_regions,
                adopted_span_ids=adopted_span_ids,
                reused=len(adopted_span_ids),
            )
        )

    results_per_page = parallel_map(
        lambda job: [recognize(job.original, region.bbox) for region in job.recognize_regions],
        pending,
        jobs=jobs,
    )

    created: list[OCRSpan] = []
    for job, results in zip(pending, results_per_page, strict=True):
        # Recompute: clear spans on ignored regions outright, and on eligible regions clear
        # everything except a detection span we're adopting, so none are orphaned.
        for region in job.regions:
            if region.status is RegionStatus.IGNORE:
                for span in store.db.list(OCRSpan, where=("region_id", region.id)):
                    store.db.delete(OCRSpan, where=("id", span.id))
        for region in job.eligible:
            for span in store.db.list(OCRSpan, where=("region_id", region.id)):
                if span.id not in job.adopted_span_ids:
                    store.db.delete(OCRSpan, where=("id", span.id))

        new_spans: list[OCRSpan] = []
        for region, result in zip(job.recognize_regions, results, strict=True):
            span = OCRSpan(
                region_id=region.id,
                text=result.text,
                confidence=result.confidence,
                alternatives=list(result.alternatives),
                source=signature,
            )
            store.db.save(span)
            new_spans.append(span)

        new_page = job.page.model_copy(
            update={
                "ocr": {
                    "signature": job.page_signature,
                    "engine": signature,
                    "count": len(job.eligible),
                    "reused": job.reused,
                }
            }
        )
        store.db.save(new_page)
        created.extend(new_spans)
    return created
