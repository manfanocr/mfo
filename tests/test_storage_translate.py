"""Tests for persisting context-aware translations per unit (§10.6; FR-21/22; I-2/3; NFR-8)."""

from __future__ import annotations

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
from mfo.core.enums import CandidateKind
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, translate_units


@dataclass(frozen=True)
class _Result:
    text: str
    confidence: float | None = None


def _echo(source: str, context: dict[str, object]) -> _Result:
    return _Result(text=f"EN[{source}]", confidence=0.9)


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


def _region(store: ProjectStore, page: Page, *, order: int, text: str | None) -> Region:
    region = Region(
        page_id=page.id,
        bbox=BBox(x=0, y=order * 50, width=40, height=40),
        reading_order_index=order,
    )
    store.db.save(region)
    if text is not None:
        store.db.save(OCRSpan(region_id=region.id, text=text))
    return region


def _unit(store: ProjectStore, page: Page, regions: list[Region]) -> TranslationUnit:
    unit = TranslationUnit(page_id=page.id, ordered_region_ids=[r.id for r in regions])
    store.db.save(unit)
    return unit


def _units(store: ProjectStore, page: Page) -> list[TranslationUnit]:
    return store.db.list(TranslationUnit, where=("page_id", page.id))


def test_translates_units_and_records_provenance(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        _unit(store, page, [region])

        units = translate_units(store, translate=_echo, signature="fake@1", target_lang="en")

        assert len(units) == 1
        unit = units[0]
        assert unit.source_bundle == "hello"  # assembled from OCR, which grouping had left empty
        assert unit.selected_candidate_id is not None
        selected = next(c for c in unit.candidates if c.id == unit.selected_candidate_id)
        assert selected.text == "EN[hello]"
        assert selected.kind is CandidateKind.RAW
        assert selected.confidence == 0.9

        saved_page = store.db.get(Page, page.id)
        assert saved_page is not None
        assert saved_page.translation["count"] == 1
        assert saved_page.translation["translator"] == "fake@1"
        assert saved_page.translation["target_lang"] == "en"
        assert saved_page.translation["signature"]


def test_source_assembled_in_reading_order(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        top = _region(store, page, order=0, text="top")
        bottom = _region(store, page, order=1, text="bottom")
        _unit(store, page, [top, bottom])

        units = translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        assert units[0].source_bundle == "top\nbottom"


def test_context_includes_neighbouring_units(tmp_path: Path) -> None:
    seen: dict[str, dict[str, object]] = {}

    def capture(source: str, context: dict[str, object]) -> _Result:
        seen[source] = context
        return _Result(text=source)

    with _store(tmp_path / "proj") as store:
        page = _page(store)
        first = _region(store, page, order=0, text="first")
        second = _region(store, page, order=1, text="second")
        _unit(store, page, [first])
        _unit(store, page, [second])

        translate_units(store, translate=capture, signature="fake@1", target_lang="en")

        assert seen["second"]["preceding"] == ["first"]
        assert seen["first"]["following"] == ["second"]


def test_candidates_survive_reopen(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        _unit(store, page, [region])
        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")

    with ProjectStore.open(tmp_path / "proj") as reopened:
        unit = _units(reopened, page)[0]
        selected = next(c for c in unit.candidates if c.id == unit.selected_candidate_id)
        assert selected.text == "EN[hello]"


def test_idempotent_skips_when_current(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        _unit(store, page, [region])

        first = translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        second = translate_units(store, translate=_echo, signature="fake@1", target_lang="en")

        assert len(first) == 1
        assert second == []  # unchanged source + params → page skipped


def test_reocr_invalidates_translation(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        _unit(store, page, [region])
        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")

        # Simulate a re-OCR: the region's recognized text changes.
        store.db.delete(OCRSpan, where=("region_id", region.id))
        store.db.save(OCRSpan(region_id=region.id, text="changed"))

        rerun = translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        assert len(rerun) == 1
        assert rerun[0].source_bundle == "changed"


def test_force_recompute_keeps_a_single_machine_candidate(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        _unit(store, page, [region])

        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        translate_units(store, translate=_echo, signature="fake@1", target_lang="en", force=True)

        unit = _units(store, page)[0]
        raw = [c for c in unit.candidates if c.kind is CandidateKind.RAW]
        assert len(raw) == 1  # forced recompute replaced, not appended


def test_preserves_human_candidate_and_selection(tmp_path: Path) -> None:
    # A human translation and its selection survive automation re-running (I-3).
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        manual = TranslationCandidate(text="human wording", kind=CandidateKind.MANUAL)
        unit = TranslationUnit(
            page_id=page.id,
            ordered_region_ids=[region.id],
            candidates=[manual],
            selected_candidate_id=manual.id,
        )
        store.db.save(unit)

        translate_units(store, translate=_echo, signature="fake@1", target_lang="en")

        saved = _units(store, page)[0]
        assert saved.selected_candidate_id == manual.id  # human selection untouched
        kinds = {c.kind for c in saved.candidates}
        assert CandidateKind.MANUAL in kinds and CandidateKind.RAW in kinds
        assert any(c.id == manual.id and c.text == "human wording" for c in saved.candidates)


def test_page_without_units_is_skipped(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        _page(store)
        assert translate_units(store, translate=_echo, signature="fake@1", target_lang="en") == []
