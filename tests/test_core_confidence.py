"""Tests for the pure confidence aggregation logic (§I-4, FR-12; MVP-11)."""

from __future__ import annotations

from mfo.core.confidence import DEFAULT_THRESHOLD, aggregate_confidence, is_low_confidence
from mfo.core.geometry import BBox
from mfo.core.models import OCRSpan, Region


def _region(confidence: float | None) -> Region:
    return Region(page_id="pg_x", bbox=BBox(x=0, y=0, width=1, height=1), confidence=confidence)


def _span(confidence: float | None) -> OCRSpan:
    return OCRSpan(region_id="rgn_x", text="t", confidence=confidence)


def test_aggregate_takes_the_weakest_signal() -> None:
    assert aggregate_confidence(_region(0.9), [_span(0.3)]) == 0.3
    assert aggregate_confidence(_region(0.2), [_span(0.8)]) == 0.2


def test_aggregate_ignores_unknown_values() -> None:
    # manga-ocr reports no score; the region falls back to its detection confidence.
    assert aggregate_confidence(_region(0.7), [_span(None)]) == 0.7
    assert aggregate_confidence(_region(None), [_span(0.4)]) == 0.4


def test_aggregate_is_none_when_nothing_known() -> None:
    assert aggregate_confidence(_region(None), [_span(None)]) is None
    assert aggregate_confidence(_region(None), []) is None


def test_is_low_confidence_threshold_and_unknown() -> None:
    assert is_low_confidence(0.4, threshold=0.5) is True
    assert is_low_confidence(0.5, threshold=0.5) is False  # strictly below is low
    assert is_low_confidence(0.9, threshold=0.5) is False
    # Unknown counts as low so uncertainty stays visible (I-4).
    assert is_low_confidence(None) is True
    assert is_low_confidence(DEFAULT_THRESHOLD) is False
