"""Tests for persisting reading order per page (§10.5; FR-16, FR-17, FR-20; I-2, I-3, NFR-8)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import Page, Project, Region
from mfo.core.enums import ReadingDirection
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, assign_reading_order


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def _page(store: ProjectStore, index: int = 0) -> Page:
    page = Page(
        project_id=store.project.id,
        index=index,
        image_path=f"originals/p{index}.png",
        width=100,
        height=100,
    )
    store.db.save(page)
    return page


def _region(store: ProjectStore, page: Page, x: float, y: float) -> Region:
    region = Region(page_id=page.id, bbox=BBox(x=x, y=y, width=40, height=40))
    store.db.save(region)
    return region


def _order_by_index(store: ProjectStore, page: Page) -> list[str]:
    regions = store.db.list(Region, where=("page_id", page.id))
    ranked = sorted(regions, key=lambda r: r.reading_order_index or 0)
    return [r.id for r in ranked]


def test_assigns_rtl_order_and_records_provenance(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        top_left = _region(store, page, x=0, y=0)
        top_right = _region(store, page, x=60, y=0)
        bottom_right = _region(store, page, x=60, y=60)

        assign_reading_order(store, direction=ReadingDirection.RTL)

        assert _order_by_index(store, page) == [top_right.id, top_left.id, bottom_right.id]
        saved = store.db.get(Page, page.id)
        assert saved is not None
        assert saved.structure["direction"] == "rtl"
        assert saved.structure["count"] == 3
        assert saved.structure["signature"]


def test_order_survives_reopen(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        left = _region(store, page, x=0, y=0)
        right = _region(store, page, x=60, y=0)
        assign_reading_order(store, direction=ReadingDirection.RTL)

    with ProjectStore.open(tmp_path / "proj") as reopened:
        assert _order_by_index(reopened, page) == [right.id, left.id]


def test_idempotent_skips_when_current(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _region(store, page, x=0, y=0)
        _region(store, page, x=60, y=0)

        first = assign_reading_order(store, direction=ReadingDirection.RTL)
        second = assign_reading_order(store, direction=ReadingDirection.RTL)

        assert len(first) == 2
        assert second == []  # unchanged regions + direction → page skipped


def test_direction_change_recomputes(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        left = _region(store, page, x=0, y=0)
        right = _region(store, page, x=60, y=0)

        assign_reading_order(store, direction=ReadingDirection.RTL)
        assert _order_by_index(store, page) == [right.id, left.id]

        rerun = assign_reading_order(store, direction=ReadingDirection.LTR)
        assert len(rerun) == 2  # direction changed → re-derived
        assert _order_by_index(store, page) == [left.id, right.id]


def test_preserves_region_ids(tmp_path: Path) -> None:
    # Reading order updates the index in place; IDs stay stable for traceability (I-2).
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        ids_before = {_region(store, page, x=0, y=0).id, _region(store, page, x=60, y=0).id}
        assign_reading_order(store, direction=ReadingDirection.RTL)
        ids_after = {r.id for r in store.db.list(Region, where=("page_id", page.id))}
        assert ids_after == ids_before


def test_redetection_invalidates_order(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _region(store, page, x=0, y=0)
        assign_reading_order(store, direction=ReadingDirection.RTL)

        # Simulate a re-detection: drop the region and create a new one with a new id.
        store.db.delete(Region, where=("page_id", page.id))
        _region(store, page, x=5, y=5)

        rerun = assign_reading_order(store, direction=ReadingDirection.RTL)
        assert len(rerun) == 1  # regions changed → order re-runs


def test_manual_order_survives_rerun_but_force_overrides(tmp_path: Path) -> None:
    # A human correction (FR-20) is preserved on a plain re-run (I-3); --force re-derives it.
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        left = _region(store, page, x=0, y=0)
        right = _region(store, page, x=60, y=0)
        assign_reading_order(store, direction=ReadingDirection.RTL)
        assert _order_by_index(store, page) == [right.id, left.id]

        # User manually reorders, then a plain re-run leaves it untouched (signature unchanged).
        store.db.save(left.model_copy(update={"reading_order_index": 0}))
        store.db.save(right.model_copy(update={"reading_order_index": 1}))
        assert assign_reading_order(store, direction=ReadingDirection.RTL) == []
        assert _order_by_index(store, page) == [left.id, right.id]

        # An explicit force re-derives the geometric order.
        assign_reading_order(store, direction=ReadingDirection.RTL, force=True)
        assert _order_by_index(store, page) == [right.id, left.id]


def test_page_without_regions_is_skipped(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _page(store)
        assert assign_reading_order(store, direction=ReadingDirection.RTL) == []


# -- Panel-aware ordering (batch 3.3; FR-18) ----------------------------------------------------

# The tricky layout, with one detected panel per frame; the detector is injected (no image needed).
_PANELS = [
    BBox(x=10, y=10, width=80, height=180),  # left, full height
    BBox(x=110, y=10, width=80, height=80),  # right-top
    BBox(x=110, y=110, width=80, height=80),  # right-bottom
]


def _fake_panels(_path: Path) -> list[BBox]:
    return list(_PANELS)


def _tricky_regions(store: ProjectStore, page: Page) -> dict[str, str]:
    return {
        "rt": _region(store, page, x=140, y=40).id,
        "l": _region(store, page, x=40, y=90).id,
        "rb": _region(store, page, x=140, y=140).id,
    }


def test_panel_aware_reorders_and_records_provenance(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        ids = _tricky_regions(store, page)

        assign_reading_order(store, direction=ReadingDirection.RTL, detect_panels=_fake_panels)

        # Right column first (top→bottom), then the left panel — not the flat rt, l, rb.
        assert _order_by_index(store, page) == [ids["rt"], ids["rb"], ids["l"]]
        saved = store.db.get(Page, page.id)
        assert saved is not None
        assert saved.structure["panels"] is True
        assert saved.structure["panel_count"] == 3


def test_flat_mode_records_no_panels(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _tricky_regions(store, page)
        assign_reading_order(store, direction=ReadingDirection.RTL)
        saved = store.db.get(Page, page.id)
        assert saved is not None
        assert saved.structure["panels"] is False
        assert "panel_count" not in saved.structure


def test_toggling_panel_mode_recomputes(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        ids = _tricky_regions(store, page)

        assign_reading_order(store, direction=ReadingDirection.RTL)
        assert _order_by_index(store, page) == [ids["rt"], ids["l"], ids["rb"]]  # flat

        # Enabling panels changes the signature, so the page re-runs rather than being skipped.
        rerun = assign_reading_order(
            store, direction=ReadingDirection.RTL, detect_panels=_fake_panels
        )
        assert len(rerun) == 3
        assert _order_by_index(store, page) == [ids["rt"], ids["rb"], ids["l"]]  # panel-aware


def test_panel_mode_is_idempotent(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _tricky_regions(store, page)

        first = assign_reading_order(
            store, direction=ReadingDirection.RTL, detect_panels=_fake_panels
        )
        second = assign_reading_order(
            store, direction=ReadingDirection.RTL, detect_panels=_fake_panels
        )
        assert len(first) == 3
        assert second == []  # unchanged regions + direction + panel mode → skipped
