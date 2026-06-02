"""Tests for persisting dialogue grouping per page (§10.5; FR-11, FR-19; I-2, I-3, NFR-8)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import Page, Project, Region, TranslationUnit
from mfo.core.enums import RegionType
from mfo.core.geometry import BBox
from mfo.core.grouping import DEFAULT_GAP_RATIO
from mfo.storage import ProjectStore, group_into_units


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def _page(store: ProjectStore, index: int = 0) -> Page:
    page = Page(
        project_id=store.project.id,
        index=index,
        image_path=f"originals/p{index}.png",
        width=200,
        height=400,
    )
    store.db.save(page)
    return page


def _region(
    store: ProjectStore,
    page: Page,
    x: float,
    y: float,
    *,
    order: int,
    type: RegionType = RegionType.BUBBLE,
) -> Region:
    region = Region(
        page_id=page.id,
        bbox=BBox(x=x, y=y, width=40, height=40),
        type=type,
        reading_order_index=order,
    )
    store.db.save(region)
    return region


def _units(store: ProjectStore, page: Page) -> list[TranslationUnit]:
    return store.db.list(TranslationUnit, where=("page_id", page.id))


def test_groups_chains_and_records_provenance(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        top = _region(store, page, x=0, y=0, order=0)
        bottom = _region(store, page, x=0, y=44, order=1)  # chains with top
        far = _region(store, page, x=0, y=200, order=2)  # separate utterance

        units = group_into_units(store)

        assert [u.ordered_region_ids for u in units] == [[top.id, bottom.id], [far.id]]
        assert all(u.page_id == page.id for u in units)
        assert all(u.source_bundle == "" for u in units)  # OCR text is filled later (M4)

        saved = store.db.get(Page, page.id)
        assert saved is not None
        assert saved.grouping["count"] == 2
        assert saved.grouping["max_gap_ratio"] == DEFAULT_GAP_RATIO
        assert saved.grouping["signature"]


def test_units_survive_reopen(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        a = _region(store, page, x=0, y=0, order=0)
        b = _region(store, page, x=0, y=44, order=1)
        group_into_units(store)

    with ProjectStore.open(tmp_path / "proj") as reopened:
        units = _units(reopened, page)
        assert [u.ordered_region_ids for u in units] == [[a.id, b.id]]


def test_idempotent_skips_when_current(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _region(store, page, x=0, y=0, order=0)
        _region(store, page, x=0, y=200, order=1)

        first = group_into_units(store)
        second = group_into_units(store)

        assert len(first) == 2
        assert second == []  # unchanged regions + params → page skipped


def test_gap_ratio_change_recomputes(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _region(store, page, x=0, y=0, order=0)
        _region(store, page, x=0, y=44, order=1)

        assert len(group_into_units(store)) == 1  # default chains the pair
        rerun = group_into_units(store, max_gap_ratio=0.0)
        assert len(rerun) == 2  # tighter threshold splits them → re-derived
        assert len(_units(store, page)) == 2


def test_recompute_replaces_prior_units(tmp_path: Path) -> None:
    # A forced recompute drops the page's prior units first, leaving no orphans (idempotent).
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _region(store, page, x=0, y=0, order=0)
        _region(store, page, x=0, y=44, order=1)

        group_into_units(store)
        group_into_units(store, force=True)
        assert len(_units(store, page)) == 1


def test_redetection_invalidates_grouping(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _region(store, page, x=0, y=0, order=0)
        _region(store, page, x=0, y=200, order=1)
        group_into_units(store)

        # Simulate a re-detection: replace the regions with new ids/geometry.
        store.db.delete(Region, where=("page_id", page.id))
        _region(store, page, x=0, y=0, order=0)
        _region(store, page, x=0, y=44, order=1)

        rerun = group_into_units(store)
        assert len(rerun) == 1  # regions changed → grouping re-runs
        assert len(_units(store, page)) == 1


def test_page_without_regions_is_skipped(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _page(store)
        assert group_into_units(store) == []
