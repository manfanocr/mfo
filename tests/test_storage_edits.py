"""Tests for the append-only edit history (EditRecord scaffolding; FR-42; I-3)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import Project
from mfo.core.enums import EditAction
from mfo.storage import ProjectStore, list_edits, record_edit


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def test_record_edit_persists_and_returns(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        record = record_edit(
            store,
            unit_id="tu_1",
            before="machine",
            after="human",
            action=EditAction.EDIT_TRANSLATION,
        )
        assert record.before == "machine"
        assert record.after == "human"
        assert record.editor == "user"

        listed = list_edits(store, "tu_1")
        assert [r.id for r in listed] == [record.id]


def test_list_edits_is_oldest_first_and_filterable(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        first = record_edit(
            store, unit_id="tu_1", before="a", after="b", action=EditAction.EDIT_TRANSLATION
        )
        second = record_edit(
            store, unit_id="tu_1", before="b", after="c", action=EditAction.EDIT_TRANSLATION
        )
        other = record_edit(
            store, unit_id="tu_2", before="x", after="y", action=EditAction.SELECT_CANDIDATE
        )

        for_unit = list_edits(store, "tu_1")
        assert [r.id for r in for_unit] == [first.id, second.id]  # append-only, chronological

        all_edits = list_edits(store)
        assert {r.id for r in all_edits} == {first.id, second.id, other.id}
