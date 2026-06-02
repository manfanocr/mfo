"""Surface and persist region confidence across a project (spec I-4, FR-12, NFR-4; MVP-11).

Aggregates each region's detection + OCR confidence (see :mod:`mfo.core.confidence`) so the CLI
and review editor can report and highlight where to look. ``flag_low_confidence`` persists the
verdict by marking low-confidence regions ``NEEDS_REVIEW`` for downstream highlighting — but only
``AUTO`` regions, so a human's status decision is never overwritten (invariant I-3).
"""

from __future__ import annotations

from dataclasses import dataclass

from mfo.core import OCRSpan, Region
from mfo.core.confidence import DEFAULT_THRESHOLD, aggregate_confidence, is_low_confidence
from mfo.core.enums import RegionStatus
from mfo.storage.project import ProjectStore


@dataclass(frozen=True)
class ConfidenceReport:
    """A read-only summary of region confidence for ``mfo status`` (I-4, NFR-4)."""

    total: int  # regions in the project
    scored: int  # regions with a known aggregate confidence
    low: int  # regions warranting review (below threshold or unknown)
    flagged: int  # regions currently marked NEEDS_REVIEW
    threshold: float


def _spans_by_region(store: ProjectStore) -> dict[str, list[OCRSpan]]:
    by_region: dict[str, list[OCRSpan]] = {}
    for span in store.db.list(OCRSpan):
        by_region.setdefault(span.region_id, []).append(span)
    return by_region


def region_confidences(store: ProjectStore) -> list[tuple[Region, float | None]]:
    """Every region paired with its aggregate detection+OCR confidence."""
    spans = _spans_by_region(store)
    return [
        (region, aggregate_confidence(region, spans.get(region.id, [])))
        for region in store.db.list(Region)
    ]


def low_confidence_regions(
    store: ProjectStore, *, threshold: float = DEFAULT_THRESHOLD
) -> list[Region]:
    """The regions warranting review — queryable for the review editor (MVP-11, DoD)."""
    return [
        region
        for region, confidence in region_confidences(store)
        if is_low_confidence(confidence, threshold=threshold)
    ]


def confidence_report(
    store: ProjectStore, *, threshold: float = DEFAULT_THRESHOLD
) -> ConfidenceReport:
    """Summarize region confidence for reporting (read-only; no mutation)."""
    pairs = region_confidences(store)
    return ConfidenceReport(
        total=len(pairs),
        scored=sum(1 for _, confidence in pairs if confidence is not None),
        low=sum(1 for _, confidence in pairs if is_low_confidence(confidence, threshold=threshold)),
        flagged=sum(1 for region, _ in pairs if region.status is RegionStatus.NEEDS_REVIEW),
        threshold=threshold,
    )


def flag_low_confidence(
    store: ProjectStore, *, threshold: float = DEFAULT_THRESHOLD
) -> list[Region]:
    """Mark low-confidence ``AUTO`` regions ``NEEDS_REVIEW`` for downstream highlighting (FR-40).

    Only ``AUTO`` regions are touched, so a human's status decision is never overwritten (I-3).
    Idempotent: a region already flagged is no longer ``AUTO`` and is left alone.
    """
    spans = _spans_by_region(store)
    flagged: list[Region] = []
    for region in store.db.list(Region):
        if region.status is not RegionStatus.AUTO:
            continue
        confidence = aggregate_confidence(region, spans.get(region.id, []))
        if is_low_confidence(confidence, threshold=threshold):
            updated = region.model_copy(update={"status": RegionStatus.NEEDS_REVIEW})
            store.db.save(updated)
            flagged.append(updated)
    return flagged
