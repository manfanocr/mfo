"""Tests for the source→OCR→translation mapping export (§7.6; FR-41/42/43; I-2/I-6; NFR-26)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mfo.core import (
    OCRSpan,
    Page,
    Project,
    Region,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.enums import CandidateKind, EditAction
from mfo.core.geometry import BBox
from mfo.storage import (
    ProjectStore,
    build_mapping,
    record_edit,
    translate_units,
    write_mapping,
)


@dataclass(frozen=True)
class _Result:
    text: str
    confidence: float | None = None


def _echo(source: str, context: dict[str, object]) -> _Result:
    return _Result(text=f"EN[{source}]", confidence=0.8)


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def _seed_unit(store: ProjectStore) -> tuple[Page, TranslationUnit, list[Region]]:
    page = Page(
        project_id=store.project.id, index=0, image_path="originals/p0.png", width=200, height=400
    )
    store.db.save(page)
    regions: list[Region] = []
    for order, text in ((0, "こんにちは"), (1, "world")):
        region = Region(
            page_id=page.id,
            bbox=BBox(x=0, y=order * 50, width=40, height=30),
            reading_order_index=order,
        )
        store.db.save(region)
        store.db.save(OCRSpan(region_id=region.id, text=text, confidence=0.9))
        regions.append(region)
    unit = TranslationUnit(page_id=page.id, ordered_region_ids=[r.id for r in regions])
    store.db.save(unit)
    return page, unit, regions


def test_build_mapping_traces_every_region_to_its_source(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page, unit, regions = _seed_unit(store)
        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")

        mapping = build_mapping(store)

        assert mapping["mapping_version"] == 1
        assert mapping["project"]["source_lang"] == "ja"
        assert len(mapping["units"]) == 1

        entry = mapping["units"][0]
        assert entry["unit_id"] == unit.id
        assert entry["page_id"] == page.id
        assert entry["page_index"] == 0
        assert entry["source_text"] == "こんにちは\nworld"
        assert entry["translation"] == "EN[こんにちは\nworld]"
        assert entry["selected_candidate_id"] is not None

        # DoD: every output region links back to source page, bbox, and OCR text (FR-42).
        assert [r["region_id"] for r in entry["regions"]] == [r.id for r in regions]
        first = entry["regions"][0]
        assert first["page_id"] == page.id
        assert first["bbox"] == {"x": 0.0, "y": 0.0, "width": 40.0, "height": 30.0}
        assert first["ocr"][0]["text"] == "こんにちは"
        assert first["ocr"][0]["confidence"] == 0.9

        # Translation history is preserved (the candidates).
        assert any(c["kind"] == CandidateKind.RAW.value for c in entry["candidates"])


def test_mapping_includes_edit_history(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _page, unit, _regions = _seed_unit(store)
        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        record_edit(
            store,
            unit_id=unit.id,
            before="EN[こんにちは\nworld]",
            after="Hi, world!",
            action=EditAction.EDIT_TRANSLATION,
        )

        entry = build_mapping(store)["units"][0]
        assert [e["after"] for e in entry["edits"]] == ["Hi, world!"]
        assert entry["edits"][0]["action"] == EditAction.EDIT_TRANSLATION.value


def test_mapping_reflects_selected_candidate(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _page, unit, _regions = _seed_unit(store)
        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        # A human selection wins (I-3): point the unit at a manual candidate.
        stored = store.db.get(TranslationUnit, unit.id)
        assert stored is not None
        manual = TranslationCandidate(text="Hi, world!", kind=CandidateKind.MANUAL)
        store.db.save(
            stored.model_copy(
                update={
                    "candidates": [*stored.candidates, manual],
                    "selected_candidate_id": manual.id,
                }
            )
        )

        entry = build_mapping(store)["units"][0]
        assert entry["translation"] == "Hi, world!"
        assert entry["selected_candidate_id"] == manual.id


def test_write_mapping_emits_utf8_json(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _seed_unit(store)
        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        out = tmp_path / "mapping.json"

        returned = write_mapping(store, out)

        assert returned == out
        raw = out.read_text(encoding="utf-8")
        assert "こんにちは" in raw  # ensure_ascii=False keeps source text readable
        loaded = json.loads(raw)
        assert loaded["mapping_version"] == 1
        assert loaded["units"][0]["source_text"] == "こんにちは\nworld"
