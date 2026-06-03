"""Tests for persisting detected regions per page (§10.3; FR-10/11; I-2, NFR-8)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PIL import Image

from mfo.core import OCRSpan, Page, Project, Region
from mfo.core.enums import RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, detect_regions, import_pages
from mfo.vision import DetectedRegion
from mfo.vision.ingest import discover_images


def _project_with_page(root: Path, source: Path) -> ProjectStore:
    source.mkdir()
    Image.new("RGB", (20, 30), "white").save(source / "p1.png")
    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    return store


def _stub(*candidates: DetectedRegion) -> Callable[[Path], list[DetectedRegion]]:
    return lambda _path: list(candidates)


_ONE = DetectedRegion(
    bbox=BBox(x=1, y=2, width=5, height=6), type=RegionType.BUBBLE, confidence=0.9
)


def test_persists_regions_linked_to_page_with_signature(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    with store:
        created = detect_regions(store, detect=_stub(_ONE), signature="stub@1")
        assert len(created) == 1
        page = store.db.list(Page)[0]
        region = store.db.list(Region, where=("page_id", page.id))[0]
        assert region.page_id == page.id
        assert region.type is RegionType.BUBBLE
        assert region.confidence == 0.9
        assert page.detection["signature"]
        assert page.detection["count"] == 1

    # Metadata + regions survive reopen.
    with ProjectStore.open(tmp_path / "proj") as reopened:
        assert len(reopened.db.list(Region)) == 1
        assert reopened.db.list(Page)[0].detection["count"] == 1


def test_idempotent_skips_when_current(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    calls: list[Path] = []

    def detect(path: Path) -> list[DetectedRegion]:
        calls.append(path)
        return [_ONE]

    with store:
        first = detect_regions(store, detect=detect, signature="stub@1")
        second = detect_regions(store, detect=detect, signature="stub@1")

    assert len(first) == 1
    assert second == []
    assert len(calls) == 1  # detector not invoked again


def test_force_recomputes_without_duplicating(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    with store:
        detect_regions(store, detect=_stub(_ONE, _ONE), signature="stub@1")
        again = detect_regions(store, detect=_stub(_ONE), signature="stub@1", force=True)
        assert len(again) == 1
        # Prior regions were cleared, so no stale boxes remain (2 → 1).
        assert len(store.db.list(Region)) == 1


def test_detector_change_recomputes(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    with store:
        detect_regions(store, detect=_stub(_ONE), signature="stub@1")
        rerun = detect_regions(store, detect=_stub(_ONE, _ONE), signature="other@2")
        assert len(rerun) == 2  # different detector id → fresh detection
        assert len(store.db.list(Region)) == 2


# -- det+rec detectors: recognition captured as provisional OCR spans (batch 8.0) ----------


def _rec(text: str, *, confidence: float | None = 0.8, status: RegionStatus = RegionStatus.AUTO):
    return DetectedRegion(
        bbox=BBox(x=1, y=2, width=5, height=6),
        type=RegionType.UNKNOWN,
        confidence=0.9,
        status=status,
        text=text,
        text_confidence=confidence,
    )


def test_detection_text_persisted_as_provisional_span(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    with store:
        detect_regions(store, detect=_stub(_rec("こんにちは")), signature="paddle-rec@1")
        region = store.db.list(Region)[0]
        spans = store.db.list(OCRSpan, where=("region_id", region.id))
        assert len(spans) == 1
        assert spans[0].text == "こんにちは"
        assert spans[0].confidence == 0.8
        assert spans[0].source == "paddle-rec@1"  # provenance recorded (I-2)
        assert store.db.list(Page)[0].detection["recognized"] is True


def test_detection_without_text_records_no_spans(tmp_path: Path) -> None:
    # A detection-only detector (no text) leaves OCR to the OCR stage.
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    with store:
        detect_regions(store, detect=_stub(_ONE), signature="baseline-cc@1")
        assert store.db.list(OCRSpan) == []
        assert store.db.list(Page)[0].detection["recognized"] is False


def test_ignored_box_text_is_not_recorded(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    with store:
        detect_regions(
            store, detect=_stub(_rec("x", status=RegionStatus.IGNORE)), signature="paddle-rec@1"
        )
        assert store.db.list(OCRSpan) == []
        assert store.db.list(Page)[0].detection["recognized"] is False


def test_redetection_clears_prior_provisional_spans(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    with store:
        detect_regions(store, detect=_stub(_rec("first")), signature="paddle-rec@1")
        detect_regions(store, detect=_stub(_rec("second")), signature="other@2", force=True)
        spans = store.db.list(OCRSpan)
        assert len(spans) == 1  # the stale span from the first pass was cleared, not orphaned
        assert spans[0].text == "second"
