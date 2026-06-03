"""Persist detected text regions per page (spec §10.3; FR-10, FR-11; I-2, NFR-8).

The detection callable is *injected* (the vision layer supplies it) so storage stays free of any
imaging dependency, mirroring the preprocess stage. Each page records a detection signature
(``hash(source, detector-id)``); re-running skips pages whose source and detector are unchanged
(NFR-8). When a page is (re)detected its prior regions are cleared first, so detection is
idempotent and a forced recompute never leaves stale boxes behind.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from mfo.core import OCRSpan, Page, Region
from mfo.core.enums import RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.storage.hashing import content_key, sha256_file
from mfo.storage.project import ProjectStore


class RegionCandidate(Protocol):
    """The minimum a detected region must expose to be persisted.

    A *det+rec* detector may also expose ``text``/``text_confidence`` (read defensively, like
    ``status``); when present the region's recognition is recorded as a provisional OCR span the OCR
    stage can adopt (batch 8.0).
    """

    @property
    def bbox(self) -> BBox: ...

    @property
    def type(self) -> RegionType: ...

    @property
    def confidence(self) -> float: ...


Detect = Callable[[Path], Sequence[RegionCandidate]]


def detect_regions(
    store: ProjectStore,
    *,
    detect: Detect,
    signature: str,
    force: bool = False,
) -> list[Region]:
    """Detect regions on every page, persisting them and a per-page signature. Returns new ones."""
    created: list[Region] = []
    for page in store.db.list(Page, order_by="idx"):
        original = store.layout.root / page.image_path
        source_hash = sha256_file(original)
        page_signature = content_key(source_hash, signature)

        current = page.detection
        if (
            not force
            and current.get("signature") == page_signature
            and store.db.list(Region, where=("page_id", page.id))
        ):
            continue

        # Recompute (forced or stale): drop any prior regions (and their spans, so none are
        # orphaned) before re-detecting.
        for prior in store.db.list(Region, where=("page_id", page.id)):
            store.db.delete(OCRSpan, where=("region_id", prior.id))
        store.db.delete(Region, where=("page_id", page.id))

        candidates = detect(original)
        regions: list[Region] = []
        spans: list[OCRSpan] = []
        for candidate in candidates:
            region = Region(
                page_id=page.id,
                bbox=candidate.bbox,
                type=candidate.type,
                confidence=candidate.confidence,
                # A detector may flag a doubtful box for review; default AUTO if it doesn't.
                status=getattr(candidate, "status", RegionStatus.AUTO),
            )
            regions.append(region)
            # A det+rec detector also hands back recognized text: store it as a provisional,
            # provenance-stamped span (skipping boxes it marked IGNORE) for the OCR stage to adopt.
            text = getattr(candidate, "text", None)
            if text is not None and region.status is not RegionStatus.IGNORE:
                spans.append(
                    OCRSpan(
                        region_id=region.id,
                        text=text,
                        confidence=getattr(candidate, "text_confidence", None),
                        source=signature,
                    )
                )
        store.db.save_all(regions)
        store.db.save_all(spans)
        new_page = page.model_copy(
            update={
                "detection": {
                    "signature": page_signature,
                    "detector": signature,
                    "count": len(regions),
                    # Whether this detector also recognized text, so the OCR stage knows it can
                    # adopt the provisional spans above instead of re-recognizing (batch 8.0).
                    "recognized": bool(spans),
                }
            }
        )
        store.db.save(new_page)
        created.extend(regions)
    return created
