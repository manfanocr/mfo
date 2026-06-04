"""Tests for the LLM OCR-correction storage stage (SG-7; I-3, NFR-8)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import OCRSpan, Page, Project, Region
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, correct_ocr_spans


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def _span(store: ProjectStore, page: Page, *, text: str, confidence: float | None) -> OCRSpan:
    region = Region(page_id=page.id, bbox=BBox(x=0, y=0, width=10, height=10))
    store.db.save(region)
    span = OCRSpan(region_id=region.id, text=text, confidence=confidence)
    store.db.save(span)
    return span


def _page(store: ProjectStore) -> Page:
    page = Page(project_id=store.project.id, index=0, image_path="p0.png", width=10, height=10)
    store.db.save(page)
    return page


def _suggest(_text: str) -> list[str]:
    return ["日本語"]


def test_only_low_confidence_spans_are_corrected(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        low = _span(store, page, text="ロ本語", confidence=0.2)
        high = _span(store, page, text="確実", confidence=0.95)

        updated = correct_ocr_spans(store, correct=_suggest, signature="llm@1", threshold=0.5)

        assert [s.id for s in updated] == [low.id]
        by_id = {s.id: s for s in store.db.list(OCRSpan)}
        # The recognized text is never changed — suggestions land in alternatives (I-3).
        assert by_id[low.id].text == "ロ本語"
        assert by_id[low.id].alternatives == ["日本語"]
        assert by_id[high.id].alternatives == []  # confident span left alone


def test_unknown_confidence_counts_as_low(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        span = _span(store, page, text="ロ本語", confidence=None)
        correct_ocr_spans(store, correct=_suggest, signature="llm@1", threshold=0.5)
        assert store.db.get(OCRSpan, span.id).alternatives == ["日本語"]


def test_is_idempotent_and_does_not_duplicate(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        span = _span(store, page, text="ロ本語", confidence=0.2)

        first = correct_ocr_spans(store, correct=_suggest, signature="llm@1", threshold=0.5)
        assert len(first) == 1
        # Unchanged inputs → skipped (NFR-8).
        assert correct_ocr_spans(store, correct=_suggest, signature="llm@1", threshold=0.5) == []
        # Forcing re-run doesn't append a duplicate alternative.
        correct_ocr_spans(store, correct=_suggest, signature="llm@1", threshold=0.5, force=True)
        assert store.db.get(OCRSpan, span.id).alternatives == ["日本語"]


def test_suggestion_equal_to_text_is_skipped(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        span = _span(store, page, text="日本語", confidence=0.2)
        updated = correct_ocr_spans(
            store, correct=lambda _t: ["日本語"], signature="llm@1", threshold=0.5
        )
        # The only proposal equals the existing text → nothing added, span not reported.
        assert updated == []
        assert store.db.get(OCRSpan, span.id).alternatives == []
