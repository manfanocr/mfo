"""Tests for surfacing and flagging low-confidence regions (§I-4, FR-12, NFR-4; MVP-11, I-3)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import OCRSpan, Project, Region
from mfo.core.enums import RegionStatus
from mfo.core.geometry import BBox
from mfo.storage import (
    ProjectStore,
    confidence_report,
    flag_low_confidence,
    low_confidence_regions,
)


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def _region(
    store: ProjectStore, confidence: float | None, status: RegionStatus = RegionStatus.AUTO
) -> Region:
    region = Region(
        page_id="pg_x", bbox=BBox(x=0, y=0, width=1, height=1), confidence=confidence, status=status
    )
    store.db.save(region)
    return region


def _status(store: ProjectStore, region_id: str) -> RegionStatus:
    region = store.db.get(Region, region_id)
    assert region is not None
    return region.status


def test_low_confidence_regions_are_queryable(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        high = _region(store, 0.9)
        low = _region(store, 0.2)
        unknown = _region(store, None)

        flagged = {region.id for region in low_confidence_regions(store, threshold=0.5)}
        assert flagged == {low.id, unknown.id}
        assert high.id not in flagged


def test_ocr_confidence_pulls_a_region_below_threshold(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        region = _region(store, 0.95)  # confidently detected...
        store.db.save(OCRSpan(region_id=region.id, text="?", confidence=0.1))  # ...poorly read

        assert [r.id for r in low_confidence_regions(store, threshold=0.5)] == [region.id]


def test_report_counts(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _region(store, 0.9)
        _region(store, 0.2)
        _region(store, None)

        report = confidence_report(store, threshold=0.5)
        assert report.total == 3
        assert report.scored == 2  # only the two with a known score
        assert report.low == 2  # the 0.2 and the unknown
        assert report.flagged == 0
        assert report.threshold == 0.5


def test_flag_marks_only_auto_regions(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        low_auto = _region(store, 0.2)
        # A low region a human already decided on must not be touched (I-3).
        low_manual = _region(store, 0.1, status=RegionStatus.CORRECT)
        high = _region(store, 0.9)

        flagged = flag_low_confidence(store, threshold=0.5)
        assert [r.id for r in flagged] == [low_auto.id]

        assert _status(store, low_auto.id) is RegionStatus.NEEDS_REVIEW
        assert _status(store, low_manual.id) is RegionStatus.CORRECT
        assert _status(store, high.id) is RegionStatus.AUTO


def test_flag_is_idempotent(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _region(store, 0.2)
        first = flag_low_confidence(store, threshold=0.5)
        second = flag_low_confidence(store, threshold=0.5)

        assert len(first) == 1
        assert second == []  # already flagged → no longer AUTO → left alone
        assert confidence_report(store, threshold=0.5).flagged == 1
