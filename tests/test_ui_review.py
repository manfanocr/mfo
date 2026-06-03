"""Tests for the framework-free review service (spec §13.2; FR-37/42/49; I-3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mfo.core import OCRSpan, Page, Project, Region, TranslationUnit, selected_text
from mfo.core.enums import CandidateKind, EditAction, RegionType
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, import_pages, list_edits, translate_units
from mfo.ui import (
    NotFoundError,
    edit_translation,
    page_image_path,
    page_view,
    project_summary,
    select_candidate,
    unit_view,
)
from mfo.vision.ingest import discover_images


@dataclass(frozen=True)
class _Result:
    text: str
    confidence: float | None = None


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
