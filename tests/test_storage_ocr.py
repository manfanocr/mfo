"""Tests for persisting OCR spans per region (§10.4; FR-12/13/15; I-2, NFR-8)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PIL import Image

from mfo.core import OCRSpan, Page, Project, Region
from mfo.core.enums import RegionStatus, RegionType
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


# -- adopting detection-provided OCR from a det+rec detector (batch 8.0) -------------------


def _recognized_project(root: Path, source: Path, *, texts: list[str | None]) -> ProjectStore:
    """A project whose page was 'recognized' by a det+rec detector, with provisional spans.

    ``texts[i]`` is the detection text for region ``i`` (``None`` → that region got no detection
    text, so the OCR stage must still recognize it).
    """
    store = _project_with_regions(root, source, regions=len(texts))
    page = store.db.list(Page)[0]
    store.db.save(
        page.model_copy(update={"detection": {"detector": "paddle-rec@1", "recognized": True}})
    )
    for region, text in zip(store.db.list(Region, where=("page_id", page.id)), texts, strict=True):
        if text is not None:
            store.db.save(
                OCRSpan(region_id=region.id, text=text, confidence=0.8, source="paddle-rec@1")
            )
    return store


def test_reuses_detection_text_without_recognizing(tmp_path: Path) -> None:
    store = _recognized_project(tmp_path / "proj", tmp_path / "src", texts=["あ", "い"])
    calls: list[Path] = []

    def recognize(path: Path, bbox: BBox) -> RecognizedText:
        calls.append(path)
        return RecognizedText(text="SHOULD-NOT-RUN")

    with store:
        created = ocr_regions(store, recognize=recognize, signature="manga-ocr@1")
        assert calls == []  # detection text adopted; the engine never ran
        assert created == []  # nothing newly recognized
        assert {s.text for s in store.db.list(OCRSpan)} == {"あ", "い"}
        assert store.db.list(Page)[0].ocr["reused"] == 2


def test_no_reuse_detection_reocrs_everything(tmp_path: Path) -> None:
    store = _recognized_project(tmp_path / "proj", tmp_path / "src", texts=["あ", "い"])
    with store:
        created = ocr_regions(
            store, recognize=_recognizer("ENGINE"), signature="manga-ocr@1", reuse_detection=False
        )
        assert len(created) == 2  # explicit engine wins; both regions re-OCR'd
        assert {s.text for s in store.db.list(OCRSpan)} == {"ENGINE"}
        assert store.db.list(Page)[0].ocr["reused"] == 0


def test_adopts_present_recognizes_missing(tmp_path: Path) -> None:
    # One region has detection text, the other doesn't → adopt one, recognize the other.
    store = _recognized_project(tmp_path / "proj", tmp_path / "src", texts=["あ", None])
    calls: list[Path] = []

    def recognize(path: Path, bbox: BBox) -> RecognizedText:
        calls.append(path)
        return RecognizedText(text="filled")

    with store:
        created = ocr_regions(store, recognize=recognize, signature="manga-ocr@1")
        assert len(calls) == 1  # only the region lacking detection text
        assert len(created) == 1
        assert {s.text for s in store.db.list(OCRSpan)} == {"あ", "filled"}
        assert store.db.list(Page)[0].ocr["reused"] == 1


def test_reuse_is_idempotent(tmp_path: Path) -> None:
    store = _recognized_project(tmp_path / "proj", tmp_path / "src", texts=["あ", "い"])
    with store:
        ocr_regions(store, recognize=_recognizer(), signature="manga-ocr@1")
        again = ocr_regions(store, recognize=_recognizer(), signature="manga-ocr@1")
        assert again == []  # unchanged page is skipped (NFR-8)
        assert len(store.db.list(OCRSpan)) == 2


def test_ignored_regions_are_skipped(tmp_path: Path) -> None:
    # Auto-ignored panel/frame blobs must not be OCR'd (item 11); only eligible regions get spans.
    store = _project_with_regions(tmp_path / "proj", tmp_path / "src", regions=1)
    with store:
        page = store.db.list(Page)[0]
        store.db.save(
            Region(
                page_id=page.id,
                bbox=BBox(x=0, y=0, width=30, height=40),
                type=RegionType.BUBBLE,
                status=RegionStatus.IGNORE,
            )
        )
        created = ocr_regions(store, recognize=_recognizer(), signature="stub@1")
        assert len(created) == 1  # the eligible region only, not the ignored one
        ignored = next(
            r
            for r in store.db.list(Region, where=("page_id", page.id))
            if r.status is RegionStatus.IGNORE
        )
        assert store.db.list(OCRSpan, where=("region_id", ignored.id)) == []
        # And the skip is idempotent — a second pass with the eligible region OCR'd is a no-op.
        assert ocr_regions(store, recognize=_recognizer(), signature="stub@1") == []


def _project_with_pages(root: Path, source: Path, *, pages: int, regions: int) -> ProjectStore:
    source.mkdir()
    for i in range(pages):
        Image.new("RGB", (40, 60), "white").save(source / f"p{i}.png")
    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    for page in store.db.list(Page, order_by="idx"):
        store.db.save_all(
            Region(
                page_id=page.id,
                bbox=BBox(x=j, y=page.index * 10 + j, width=5, height=8),
                type=RegionType.BUBBLE,
            )
            for j in range(regions)
        )
    return store


def _texts_by_region(store: ProjectStore) -> dict[tuple[int, int, int, int], str]:
    out: dict[tuple[int, int, int, int], str] = {}
    for region in store.db.list(Region):
        spans = store.db.list(OCRSpan, where=("region_id", region.id))
        b = region.bbox
        out[(b.x, b.y, b.width, b.height)] = spans[0].text if spans else ""
    return out


def test_parallel_matches_serial_and_cache_still_skips(tmp_path: Path) -> None:
    # Recognition is a pure function of the box, so parallel and serial must agree (I-5).
    def recognize(path: Path, bbox: BBox) -> RecognizedText:
        return RecognizedText(text=f"{path.stem}:{bbox.x},{bbox.y}", confidence=0.5)

    serial = _project_with_pages(tmp_path / "s", tmp_path / "ssrc", pages=4, regions=3)
    with serial:
        ocr_regions(serial, recognize=recognize, signature="stub@1", jobs=1)
        serial_texts = _texts_by_region(serial)

    parallel = _project_with_pages(tmp_path / "p", tmp_path / "psrc", pages=4, regions=3)
    with parallel:
        created = ocr_regions(parallel, recognize=recognize, signature="stub@1", jobs=4)
        assert len(created) == 12
        assert _texts_by_region(parallel) == serial_texts
        # Unchanged pages skip even across workers (NFR-8).
        assert ocr_regions(parallel, recognize=recognize, signature="stub@1", jobs=4) == []
