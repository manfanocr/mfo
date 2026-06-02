"""Confidence aggregation for regions (spec I-4, FR-12, NFR-4; MVP-11).

A region accrues confidence from two stages: detection (how sure we are it is a text region) and
OCR (how sure we are about the transcription). This module combines them into one conservative
score and decides whether a region is *low confidence* — i.e. should be surfaced for review.
Keeping uncertainty visible rather than hidden is invariant I-4, so unknown confidence counts as
low. The logic is pure (no I/O); the storage layer applies it across a project.
"""

from __future__ import annotations

from collections.abc import Iterable

from mfo.core.models import OCRSpan, Region

# Regions scoring below this are surfaced for review by default. Tunable per invocation.
DEFAULT_THRESHOLD = 0.5


def aggregate_confidence(region: Region, spans: Iterable[OCRSpan]) -> float | None:
    """Combine a region's detection and OCR confidence into one score (the weakest signal).

    Taking the minimum is deliberately conservative: a region is only as trustworthy as its least
    certain stage. Unknown (``None``) values are ignored; if nothing is known the result is
    ``None`` (treated as low downstream, so genuine uncertainty stays visible — I-4).
    """
    present = [
        value
        for value in (region.confidence, *(span.confidence for span in spans))
        if value is not None
    ]
    return min(present) if present else None


def is_low_confidence(value: float | None, *, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """Whether a score warrants review: unknown or strictly below ``threshold`` (I-4)."""
    return value is None or value < threshold
