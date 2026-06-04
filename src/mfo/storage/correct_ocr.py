"""Attach LLM OCR corrections to low-confidence spans, as alternatives (SG-7; FR-12/13; I-3, I-7).

The *application* half of the optional OCR-correction layer: the language adapter
(:mod:`mfo.language.ocr_correct`) proposes corrected readings for an uncertain line, and this stage
records them on the span's :attr:`~mfo.core.models.OCRSpan.alternatives` — **never** changing the
recognized ``text`` (I-3). Only low-confidence spans are sent (FR-12), the correction callable is
*injected* so storage keeps no provider dependency (mirroring the translate/assist stages), and each
page records a provenance signature so re-running skips unchanged pages (NFR-8). Suggestions are
merged distinctly, so re-running never duplicates them (idempotent). Off the core path: nothing here
runs unless invoked, and a project that never runs it is unaffected (I-7).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from mfo.core import OCRSpan, Page, Region
from mfo.core.confidence import DEFAULT_THRESHOLD, is_low_confidence
from mfo.storage.hashing import content_key
from mfo.storage.project import ProjectStore

#: Injected correction callable: ``text -> proposed corrected readings`` (alternatives, best first).
Correct = Callable[[str], list[str]]


def _low_spans(store: ProjectStore, page: Page, threshold: float) -> list[OCRSpan]:
    """The page's OCR spans warranting correction: low confidence and with text to correct."""
    low: list[OCRSpan] = []
    for region in store.db.list(Region, where=("page_id", page.id)):
        for span in store.db.list(OCRSpan, where=("region_id", region.id)):
            if span.text.strip() and is_low_confidence(span.confidence, threshold=threshold):
                low.append(span)
    return low


def _spans_fingerprint(spans: list[OCRSpan]) -> str:
    """A stable digest of the targeted spans, so re-OCR (new text/confidence) re-runs correction."""
    digest = hashlib.sha256()
    for span in sorted(spans, key=lambda s: s.id):
        digest.update(f"{span.id}:{span.confidence}:{span.text}\n".encode())
    return digest.hexdigest()


def _merge_alternatives(span: OCRSpan, proposed: list[str]) -> OCRSpan | None:
    """Fold ``proposed`` readings into the span's alternatives (distinct; excluding its own text).

    Returns the updated span, or ``None`` if nothing new was added (so the caller can skip a write).
    The recognized ``text`` is never touched — corrections are suggestions only (I-3).
    """
    existing = list(span.alternatives)
    seen = {*existing, span.text}
    added: list[str] = []
    for alt in proposed:
        if alt and alt not in seen:
            seen.add(alt)
            added.append(alt)
    if not added:
        return None
    return span.model_copy(update={"alternatives": [*existing, *added]})


def correct_ocr_spans(
    store: ProjectStore,
    *,
    correct: Correct,
    signature: str,
    threshold: float = DEFAULT_THRESHOLD,
    force: bool = False,
) -> list[OCRSpan]:
    """Propose corrections for every page's low-confidence OCR spans; returns the spans updated.

    Each low-confidence span's text is sent to ``correct``; the proposed readings are appended to
    the span's alternatives (distinct, never replacing recognized text — I-3). Unchanged pages are
    skipped unless ``force`` (NFR-8).
    """
    updated: list[OCRSpan] = []
    for page in store.db.list(Page, order_by="idx"):
        low = _low_spans(store, page, threshold)
        if not low:
            continue
        page_signature = content_key(
            f"ocr-correct|{signature}|{threshold}", _spans_fingerprint(low)
        )
        if not force and page.ocr_correction.get("signature") == page_signature:
            continue

        page_updated: list[OCRSpan] = []
        for span in low:
            merged = _merge_alternatives(span, correct(span.text))
            if merged is not None:
                store.db.save(merged)
                page_updated.append(merged)

        store.db.save(
            page.model_copy(
                update={
                    "ocr_correction": {
                        "signature": page_signature,
                        "corrector": signature,
                        "threshold": threshold,
                        "suggested": len(page_updated),
                        "low_spans": len(low),
                    }
                }
            )
        )
        updated.extend(page_updated)
    return updated
