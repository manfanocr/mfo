"""Tests for persisting detected regions per page (§10.3; FR-10/11; I-2, NFR-8)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PIL import Image

from mfo.core import Page, Project, Region
from mfo.core.enums import RegionType
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
