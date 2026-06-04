"""Tests for the SQLite persistence layer: migrations and entity CRUD."""

from __future__ import annotations

from pathlib import Path

import pytest

from mfo.core import (
    Assignment,
    BBox,
    EditAction,
    EditRecord,
    OCRSpan,
    Page,
    Project,
    Region,
    RegionType,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.storage.db import SCHEMA_VERSION, Database


def test_fresh_db_is_migrated_to_current_version(tmp_path: Path) -> None:
    with Database.open(tmp_path / "project.db") as db:
        assert db.schema_version() == SCHEMA_VERSION


def test_reopen_is_idempotent_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "project.db"
    page = Page(project_id="prj_x", index=0, image_path="a.png", width=10, height=20)
    with Database.open(path) as db:
        db.save(page)
    # Reopening re-runs migrations (no error) and data survives.
    with Database.open(path) as db:
        assert db.schema_version() == SCHEMA_VERSION
        assert db.get(Page, page.id) == page


def test_save_get_round_trip(tmp_path: Path) -> None:
    with Database.open(tmp_path / "project.db") as db:
        candidate = TranslationCandidate(text="Hello")
        entities = [
            Page(project_id="prj_x", index=0, image_path="a.png", width=1, height=1),
            Region(page_id="pg_x", bbox=BBox(x=0, y=0, width=5, height=5), type=RegionType.BUBBLE),
            OCRSpan(region_id="rgn_x", text="こんにちは", confidence=0.9),
            TranslationUnit(candidates=[candidate], selected_candidate_id=candidate.id),
            EditRecord(
                translation_unit_id="tu_x",
                before="Hi",
                after="Hello",
                action=EditAction.EDIT_TRANSLATION,
            ),
            RenderArtifact(page_id="pg_x", output_path="renders/000.png"),
            Assignment(page_id="pg_x", editor="alice"),
        ]
        for entity in entities:
            db.save(entity)
            assert db.get(type(entity), entity.id) == entity


def test_assignments_are_queryable_by_page(tmp_path: Path) -> None:
    with Database.open(tmp_path / "project.db") as db:
        db.save(Assignment(page_id="pg_1", editor="alice"))
        db.save(Assignment(page_id="pg_2", editor="bob"))
        claims = db.list(Assignment, where=("page_id", "pg_1"))
        assert [a.editor for a in claims] == ["alice"]


def test_get_missing_returns_none(tmp_path: Path) -> None:
    with Database.open(tmp_path / "project.db") as db:
        assert db.get(Region, "rgn_missing") is None


def test_list_order_by_and_filter(tmp_path: Path) -> None:
    with Database.open(tmp_path / "project.db") as db:
        db.save_all(
            [
                Page(project_id="prj", index=2, image_path="2.png", width=1, height=1),
                Page(project_id="prj", index=0, image_path="0.png", width=1, height=1),
                Page(project_id="prj", index=1, image_path="1.png", width=1, height=1),
            ]
        )
        ordered = db.list(Page, order_by="idx")
        assert [page.index for page in ordered] == [0, 1, 2]

        region_a = Region(page_id="pg_a", bbox=BBox(x=0, y=0, width=1, height=1))
        region_b = Region(page_id="pg_b", bbox=BBox(x=0, y=0, width=1, height=1))
        db.save_all([region_a, region_b])
        only_a = db.list(Region, where=("page_id", "pg_a"))
        assert [region.id for region in only_a] == [region_a.id]


def test_invalid_column_is_rejected(tmp_path: Path) -> None:
    with Database.open(tmp_path / "project.db") as db, pytest.raises(ValueError):
        db.list(Page, order_by="data; DROP TABLE pages")


def test_non_persisted_entity_is_rejected(tmp_path: Path) -> None:
    with Database.open(tmp_path / "project.db") as db, pytest.raises(TypeError):
        db.save(Project(name="x", source_lang="ja", target_lang="en"))
