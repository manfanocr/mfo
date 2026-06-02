"""Tests for persisting OCR spans per region (§10.4; FR-12/13/15; I-2, NFR-8)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PIL import Image

from mfo.core import OCRSpan, Page, Project, Region
from mfo.core.enums import RegionType
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, import_pages, ocr_regions
from mfo.vision import RecognizedText
from mfo.vision.ingest import discover_images


def _project_with_regions(root: Path, source: Path, *, regions: int = 2) -> ProjectStore:
    source.mkdir()
    Image.new("RGB", (40, 60), "white").save(source / "p1.png")
    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    page = store.db.list(Page)[0]
    store.db.save_all(
        Region(page_id=page.id, bbox=BBox(x=i, y=i, width=5, height=8), type=RegionType.BUBBLE)
        for i in range(regions)
    )
    return store


def _recognizer(text: str = "テスト") -> Callable[[Path, BBox], RecognizedText]:
    return lambda _path, _bbox: RecognizedText(text=text, confidence=0.7, alternatives=["alt"])


def test_persists_spans_linked_to_regions_with_signature(tmp_path: Path) -> None:
    store = _project_with_regions(tmp_path / "proj", tmp_path / "src", regions=2)
    with store:
        created = ocr_regions(store, recognize=_recognizer(), signature="stub@1")
        assert len(created) == 2
        page = store.db.list(Page)[0]
        for region in store.db.list(Region, where=("page_id", page.id)):
            spans = store.db.list(OCRSpan, where=("region_id", region.id))
            assert len(spans) == 1
            assert spans[0].text == "テスト"
            assert spans[0].confidence == 0.7
            assert spans[0].alternatives == ["alt"]
        assert page.ocr["signature"]
        assert page.ocr["count"] == 2

    # Spans + provenance survive reopen.
    with ProjectStore.open(tmp_path / "proj") as reopened:
        assert len(reopened.db.list(OCRSpan)) == 2
        assert reopened.db.list(Page)[0].ocr["count"] == 2


def test_idempotent_skips_when_current(tmp_path: Path) -> None:
    store = _project_with_regions(tmp_path / "proj", tmp_path / "src")
    calls: list[Path] = []

    def recognize(path: Path, bbox: BBox) -> RecognizedText:
        calls.append(path)
        return RecognizedText(text="あ")

    with store:
        first = ocr_regions(store, recognize=recognize, signature="stub@1")
        second = ocr_regions(store, recognize=recognize, signature="stub@1")

    assert len(first) == 2
    assert second == []
    assert len(calls) == 2  # only the first pass invoked the recognizer (once per region)


def test_force_recomputes_without_duplicating(tmp_path: Path) -> None:
    store = _project_with_regions(tmp_path / "proj", tmp_path / "src", regions=2)
    with store:
        ocr_regions(store, recognize=_recognizer("first"), signature="stub@1")
        again = ocr_regions(store, recognize=_recognizer("second"), signature="stub@1", force=True)
        assert len(again) == 2
        spans = store.db.list(OCRSpan)
        assert len(spans) == 2  # prior spans cleared, not duplicated
        assert {span.text for span in spans} == {"second"}


def test_engine_change_recomputes(tmp_path: Path) -> None:
    store = _project_with_regions(tmp_path / "proj", tmp_path / "src")
    with store:
        ocr_regions(store, recognize=_recognizer(), signature="stub@1")
        rerun = ocr_regions(store, recognize=_recognizer(), signature="other@2")
        assert len(rerun) == 2  # different engine id → fresh OCR
        assert len(store.db.list(OCRSpan)) == 2


def test_redetection_invalidates_ocr(tmp_path: Path) -> None:
    store = _project_with_regions(tmp_path / "proj", tmp_path / "src", regions=1)
    with store:
        ocr_regions(store, recognize=_recognizer(), signature="stub@1")
        page = store.db.list(Page)[0]
        # Simulate a re-detection: drop the region and create a new one with a new id.
        store.db.delete(Region, where=("page_id", page.id))
        store.db.save(Region(page_id=page.id, bbox=BBox(x=2, y=2, width=9, height=9)))

        rerun = ocr_regions(store, recognize=_recognizer(), signature="stub@1")
        assert len(rerun) == 1  # regions changed → OCR re-runs despite the same engine id


def test_page_without_regions_is_skipped(tmp_path: Path) -> None:
    store = _project_with_regions(tmp_path / "proj", tmp_path / "src", regions=0)
    with store:
        assert ocr_regions(store, recognize=_recognizer(), signature="stub@1") == []
        assert store.db.list(OCRSpan) == []
