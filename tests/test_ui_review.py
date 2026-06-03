"""Tests for the framework-free review service (spec §13.2; FR-37/42/49; I-3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mfo.core import OCRSpan, Page, Project, Region, TranslationUnit, selected_text
from mfo.core.enums import CandidateKind, EditAction, RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, import_pages, list_edits, translate_units
from mfo.ui import (
    NotFoundError,
    create_region,
    delete_region,
    edit_translation,
    merge_regions,
    move_region,
    page_image_path,
    page_render_path,
    page_view,
    project_summary,
    reorder_regions,
    rerender_page,
    review_queue,
    select_candidate,
    set_region_status,
    split_region,
    unit_view,
)
from mfo.vision.ingest import discover_images


@dataclass(frozen=True)
class _Result:
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class _Recognized:
    """An OCR result for the create-region tests (the recognize callable's return shape)."""

    text: str
    confidence: float | None = None
    alternatives: list[str] = field(default_factory=list)


def _echo(source: str, context: dict[str, object]) -> _Result:
    return _Result(text=f"EN[{source}]", confidence=0.8)


def _project_with_unit(root: Path, source: Path) -> ProjectStore:
    """A project with one imported page carrying a single translated bubble unit."""
    source.mkdir()
    arr = np.full((100, 100, 3), 255, dtype=np.uint8)
    arr[15:40, 15:70] = 0
    Image.fromarray(arr, mode="RGB").save(source / "p1.png")

    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    page = store.db.list(Page)[0]

    region = Region(
        page_id=page.id,
        bbox=BBox(x=15, y=15, width=55, height=25),
        reading_order_index=0,
        type=RegionType.BUBBLE,
        confidence=0.3,  # low, to exercise the confidence surfacing
    )
    store.db.save(region)
    store.db.save(OCRSpan(region_id=region.id, text="こんにちは", confidence=0.9))
    store.db.save(TranslationUnit(page_id=page.id, ordered_region_ids=[region.id]))
    translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
    return store


def _unit(store: ProjectStore) -> TranslationUnit:
    return store.db.list(TranslationUnit)[0]


def _project_with_two_regions(root: Path, source: Path) -> ProjectStore:
    """A page with two bubble regions in one unit, for the region operations (§13.3/13.4)."""
    source.mkdir()
    arr = np.full((120, 120, 3), 255, dtype=np.uint8)
    Image.fromarray(arr, mode="RGB").save(source / "p1.png")

    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    page = store.db.list(Page)[0]

    a = Region(
        page_id=page.id,
        bbox=BBox(x=10, y=10, width=40, height=20),
        reading_order_index=0,
        type=RegionType.BUBBLE,
        confidence=0.2,  # low
    )
    b = Region(
        page_id=page.id,
        bbox=BBox(x=10, y=60, width=40, height=20),
        reading_order_index=1,
        type=RegionType.BUBBLE,
        confidence=0.9,  # high
    )
    store.db.save(a)
    store.db.save(b)
    store.db.save(OCRSpan(region_id=a.id, text="A", confidence=0.2))
    store.db.save(OCRSpan(region_id=b.id, text="B", confidence=0.9))
    store.db.save(TranslationUnit(page_id=page.id, ordered_region_ids=[a.id, b.id]))
    return store


def _regions_in_order(store: ProjectStore) -> list[Region]:
    return sorted(
        store.db.list(Region),
        key=lambda r: r.reading_order_index if r.reading_order_index is not None else 1 << 30,
    )


# -- read views ---------------------------------------------------------------------------


def test_project_summary_indexes_pages_with_counts(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        summary = project_summary(store)

        assert summary["project"]["name"] == "vol"
        assert summary["project"]["source_lang"] == "ja"
        assert len(summary["pages"]) == 1
        page = summary["pages"][0]
        assert page["regions"] == 1
        assert page["units"] == 1
        assert page["low_confidence"] == 1  # region confidence 0.3 < 0.5


def test_page_view_exposes_regions_ocr_and_units(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        view = page_view(store, page.id)

        assert view["page_id"] == page.id
        assert len(view["regions"]) == 1
        region = view["regions"][0]
        assert region["type"] == "bubble"
        assert region["ocr"][0]["text"] == "こんにちは"
        assert region["low_confidence"] is True  # confidence surfaced, not hidden (I-4)

        assert len(view["units"]) == 1
        unit = view["units"][0]
        assert unit["translation"] == "EN[こんにちは]"
        assert unit["candidates"][0]["kind"] == "raw"
        assert unit["edits"] == []


def test_page_view_unknown_page_raises(tmp_path: Path) -> None:
    with (
        _project_with_unit(tmp_path / "proj", tmp_path / "src") as store,
        pytest.raises(NotFoundError),
    ):
        page_view(store, "pg_missing")


def test_page_image_path_points_at_the_source(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        path = page_image_path(store, page.id)
        assert path.is_file()
        assert path == store.layout.root / page.image_path


# -- mutations ----------------------------------------------------------------------------


def test_edit_translation_records_manual_candidate_and_edit(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        unit = _unit(store)
        result = edit_translation(store, unit.id, "Hello!")

        assert result["translation"] == "Hello!"
        assert result["selected_candidate_id"] == result["candidates"][-1]["id"]
        assert result["candidates"][-1]["kind"] == "manual"

        saved = _unit(store)
        assert selected_text(saved) == "Hello!"

        edits = list_edits(store, unit.id)
        assert len(edits) == 1
        assert edits[0].action is EditAction.EDIT_TRANSLATION
        assert edits[0].before == "EN[こんにちは]"
        assert edits[0].after == "Hello!"


def test_repeated_edits_reuse_one_manual_candidate(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        unit = _unit(store)
        edit_translation(store, unit.id, "Hi")
        edit_translation(store, unit.id, "Hello there")

        saved = _unit(store)
        manual = [c for c in saved.candidates if c.kind is CandidateKind.MANUAL]
        assert len(manual) == 1  # reused, not duplicated
        assert selected_text(saved) == "Hello there"
        assert len(list_edits(store, unit.id)) == 2  # but both edits are recorded (FR-42)


def test_select_candidate_reverts_to_machine_and_records(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        unit = _unit(store)
        raw_id = unit.candidates[0].id
        edit_translation(store, unit.id, "Manual text")

        result = select_candidate(store, unit.id, raw_id)
        assert result["translation"] == "EN[こんにちは]"
        assert result["selected_candidate_id"] == raw_id

        edits = list_edits(store, unit.id)
        assert edits[-1].action is EditAction.SELECT_CANDIDATE
        assert edits[-1].before == "Manual text"
        assert edits[-1].after == "EN[こんにちは]"


def test_select_unknown_candidate_raises(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        unit = _unit(store)
        with pytest.raises(NotFoundError):
            select_candidate(store, unit.id, "cand_missing")


def test_edit_unknown_unit_raises(tmp_path: Path) -> None:
    with (
        _project_with_unit(tmp_path / "proj", tmp_path / "src") as store,
        pytest.raises(NotFoundError),
    ):
        edit_translation(store, "tu_missing", "x")


def test_user_edit_survives_retranslation(tmp_path: Path) -> None:
    """A manual edit must win over automation: re-translating preserves it (I-3, FR-29)."""
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        unit = _unit(store)
        edit_translation(store, unit.id, "Approved by human")

        translate_units(store, translate=_echo, signature="fake@1", target_lang="en", force=True)

        saved = _unit(store)
        assert selected_text(saved) == "Approved by human"
        view = unit_view(store, unit.id)
        assert view["translation"] == "Approved by human"


# -- region operations (§13.3/13.4; FR-20/38/39/40) ---------------------------------------


def test_set_region_status_persists_and_returns_page(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        region = store.db.list(Region)[0]
        view = set_region_status(store, region.id, "correct")

        assert view["regions"][0]["status"] == "correct"
        assert store.db.get(Region, region.id).status is RegionStatus.CORRECT


def test_set_region_status_rejects_unknown_value(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        region = store.db.list(Region)[0]
        with pytest.raises(ValueError):
            set_region_status(store, region.id, "bogus")


def test_move_region_updates_bbox(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        region = store.db.list(Region)[0]
        view = move_region(store, region.id, x=5, y=6, width=70, height=30)

        bbox = view["regions"][0]["bbox"]
        assert (bbox["x"], bbox["y"], bbox["width"], bbox["height"]) == (5, 6, 70, 30)
        saved = store.db.get(Region, region.id)
        assert (saved.bbox.x, saved.bbox.width) == (5, 70)


def test_move_region_rejects_negative_size(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        region = store.db.list(Region)[0]
        with pytest.raises(ValueError):
            move_region(store, region.id, x=0, y=0, width=-1, height=10)


def test_reorder_regions_applies_manual_order(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        a, b = _regions_in_order(store)
        view = reorder_regions(store, page.id, [b.id, a.id])

        assert [r["region_id"] for r in view["regions"]] == [b.id, a.id]
        assert store.db.get(Region, b.id).reading_order_index == 0
        assert store.db.get(Region, a.id).reading_order_index == 1


def test_reorder_regions_rejects_non_permutation(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        a, _ = _regions_in_order(store)
        with pytest.raises(ValueError):
            reorder_regions(store, page.id, [a.id])


def test_split_region_creates_adjacent_region(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        a, b = _regions_in_order(store)
        view = split_region(store, a.id, orientation="horizontal", ratio=0.5)

        ids = [r["region_id"] for r in view["regions"]]
        assert len(ids) == 3
        assert ids[0] == a.id and ids[2] == b.id  # new piece inserted right after the original
        new_id = ids[1]
        original = store.db.get(Region, a.id)
        new_region = store.db.get(Region, new_id)
        assert original.bbox.height == 10  # 20 * 0.5
        assert new_region.bbox.y == 20  # 10 + 10
        # The unit picked up the new region right after the one it was split from (I-2).
        unit = store.db.list(TranslationUnit)[0]
        assert unit.ordered_region_ids == [a.id, new_id, b.id]


def test_split_region_rejects_bad_ratio(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        a, _ = _regions_in_order(store)
        with pytest.raises(ValueError):
            split_region(store, a.id, ratio=0.0)


def test_merge_regions_unions_box_and_moves_ocr(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        a, b = _regions_in_order(store)
        view = merge_regions(store, [a.id, b.id])

        assert len(view["regions"]) == 1
        survivor = store.db.get(Region, a.id)  # earliest in reading order survives
        assert survivor is not None
        assert store.db.get(Region, b.id) is None
        # Union box spans both (y 10..80).
        assert (survivor.bbox.y, survivor.bbox.height) == (10, 70)
        # Both OCR spans now hang off the survivor — no transcription lost (I-2).
        assert {s.text for s in store.db.list(OCRSpan, where=("region_id", a.id))} == {"A", "B"}
        unit = store.db.list(TranslationUnit)[0]
        assert unit.ordered_region_ids == [a.id]


def test_merge_regions_needs_two(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        a, _ = _regions_in_order(store)
        with pytest.raises(ValueError):
            merge_regions(store, [a.id])


def test_delete_region_drops_ocr_and_detaches_from_unit(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        a, b = _regions_in_order(store)
        view = delete_region(store, a.id)

        assert len(view["regions"]) == 1
        assert store.db.get(Region, a.id) is None
        assert store.db.list(OCRSpan, where=("region_id", a.id)) == []
        unit = store.db.list(TranslationUnit)[0]
        assert unit.ordered_region_ids == [b.id]  # detached from the deleted region
        # Reading order stays contiguous from 0.
        assert store.db.get(Region, b.id).reading_order_index == 0  # type: ignore[union-attr]


def test_delete_last_region_removes_empty_unit(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        region = store.db.list(Region)[0]
        delete_region(store, region.id)
        assert store.db.list(Region) == []
        assert store.db.list(TranslationUnit) == []  # the now-empty unit is gone


def test_create_region_ocrs_and_translates(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]

        def recognize(path: Path, bbox: BBox) -> _Recognized:
            return _Recognized(text="やあ", confidence=0.7)

        before_regions = len(store.db.list(Region))
        view = create_region(
            store,
            page.id,
            x=5,
            y=5,
            width=30,
            height=20,
            recognize=recognize,
            translate=_echo,
            target_lang="en",
        )

        assert len(view["regions"]) == before_regions + 1
        new_region = max(store.db.list(Region), key=lambda r: r.reading_order_index or 0)
        assert (
            new_region.status is RegionStatus.MANUAL
        )  # user-made → automation won't clobber (I-3)
        span = store.db.list(OCRSpan, where=("region_id", new_region.id))[0]
        assert span.text == "やあ"
        unit = next(
            u for u in store.db.list(TranslationUnit) if new_region.id in u.ordered_region_ids
        )
        assert selected_text(unit) == "EN[やあ]"  # OCR'd then translated immediately


def test_create_region_rejects_zero_size(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        with pytest.raises(ValueError):
            create_region(
                store,
                page.id,
                x=5,
                y=5,
                width=0,
                height=20,
                recognize=lambda path, bbox: _Result(text=""),
                translate=_echo,
                target_lang="en",
            )


# -- review queue (§13.4) -----------------------------------------------------------------


def test_review_queue_surfaces_low_confidence_first(tmp_path: Path) -> None:
    with _project_with_two_regions(tmp_path / "proj", tmp_path / "src") as store:
        a, b = _regions_in_order(store)
        queue = review_queue(store)

        entries = queue["entries"]
        assert len(entries) == 2
        assert entries[0]["region_id"] == a.id  # confidence 0.2 < 0.5 → first
        assert entries[0]["low_confidence"] is True
        assert entries[1]["region_id"] == b.id
        assert entries[1]["low_confidence"] is False


# -- re-render preview (§13.3) ------------------------------------------------------------


def test_rerender_page_produces_a_render(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        result = rerender_page(store, page.id)

        assert result["rendered"] is True
        assert result["render_url"] == f"/api/pages/{page.id}/render"
        path = page_render_path(store, page.id)
        assert path.is_file()
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_page_render_path_before_render_raises(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        with pytest.raises(NotFoundError):
            page_render_path(store, page.id)
