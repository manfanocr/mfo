"""Tests for persisting context-aware translations per unit (§10.6; FR-21/22; I-2/3; NFR-8)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mfo.core import (
    GlossaryEntry,
    OCRSpan,
    Page,
    Project,
    Region,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.enums import CandidateKind, TranslationStyle
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


def _panel_region(
    store: ProjectStore, page: Page, *, order: int, text: str, panel: int | None
) -> Region:
    region = Region(
        page_id=page.id,
        bbox=BBox(x=0, y=order * 50, width=40, height=40),
        reading_order_index=order,
        panel_index=panel,
    )
    store.db.save(region)
    store.db.save(OCRSpan(region_id=region.id, text=text))
    return region


def test_context_is_scoped_to_the_panel(tmp_path: Path) -> None:
    # With panel data, a unit's context window does not cross the frame boundary (SG-1).
    seen: dict[str, dict[str, object]] = {}

    def capture(source: str, context: dict[str, object]) -> _Result:
        seen[source] = context
        return _Result(text=source)

    with _store(tmp_path / "proj") as store:
        page = _page(store)
        # Panel 0: a, b ; panel 1: c, d — in reading order a, b, c, d.
        for order, (text, panel) in enumerate([("a", 0), ("b", 0), ("c", 1), ("d", 1)]):
            _unit(store, page, [_panel_region(store, page, order=order, text=text, panel=panel)])

        translate_units(store, translate=capture, signature="fake@1", target_lang="en")

        assert seen["b"]["panel"] == 0
        assert seen["b"]["following"] == []  # c/d are in panel 1 — no bleed across the boundary
        assert seen["c"]["panel"] == 1
        assert seen["c"]["preceding"] == []  # a/b are in panel 0
        assert seen["c"]["following"] == ["d"]


def test_no_panel_data_keeps_the_flat_window(tmp_path: Path) -> None:
    # No region carries a panel → bundle is identical to the pre-8.4 flat window (no `panel` key).
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

        assert "panel" not in seen["first"]
        assert seen["first"]["following"] == ["second"]  # flat window unaffected (offline path)


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


def test_glossary_enforced_on_machine_output(tmp_path: Path) -> None:
    # The machine renders the name as an alias; the glossary normalizes it to canonical (FR-23).
    def render(source: str, context: dict[str, object]) -> _Result:
        return _Result(text="Tarou runs")

    glossary = (GlossaryEntry(source="太郎", target="Taro", aliases=("Tarou",)),)
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="太郎")
        _unit(store, page, [region])

        units = translate_units(
            store, translate=render, signature="fake@1", target_lang="en", glossary=glossary
        )
        selected = next(c for c in units[0].candidates if c.id == units[0].selected_candidate_id)
        assert selected.text == "Taro runs"


def test_glossary_injected_into_context(tmp_path: Path) -> None:
    # Applicable terms ride along in the unit's context bundle for context-aware adapters (FR-24).
    seen: dict[str, dict[str, object]] = {}

    def capture(source: str, context: dict[str, object]) -> _Result:
        seen[source] = context
        return _Result(text=source)

    glossary = (GlossaryEntry(source="太郎", target="Taro"),)
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="太郎")
        _unit(store, page, [region])

        units = translate_units(
            store, translate=capture, signature="fake@1", target_lang="en", glossary=glossary
        )
        assert seen["太郎"]["glossary"] == [{"source": "太郎", "target": "Taro"}]
        assert units[0].context_bundle["glossary"] == [{"source": "太郎", "target": "Taro"}]


def test_style_threaded_to_translator_and_recorded(tmp_path: Path) -> None:
    def render(source: str, context: dict[str, object]) -> _Result:
        return _Result(text=source)

    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        _unit(store, page, [region])

        units = translate_units(
            store,
            translate=render,
            signature="fake@1",
            target_lang="en",
            style=TranslationStyle.NATURAL,
        )
        assert units[0].style is TranslationStyle.NATURAL
        saved_page = store.db.get(Page, page.id)
        assert saved_page is not None
        assert saved_page.translation["style"] == "natural"


def test_style_change_invalidates_cache(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="hello")
        _unit(store, page, [region])

        first = translate_units(
            store,
            translate=_echo,
            signature="fake@1",
            target_lang="en",
            style=TranslationStyle.LITERAL,
        )
        rerun = translate_units(
            store,
            translate=_echo,
            signature="fake@1",
            target_lang="en",
            style=TranslationStyle.NATURAL,
        )
        assert len(first) == 1
        assert len(rerun) == 1  # a different style is a different request → recomputed


def test_glossary_change_invalidates_cache(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _region(store, page, order=0, text="太郎")
        _unit(store, page, [region])

        first = translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
        rerun = translate_units(
            store,
            translate=_echo,
            signature="fake@1",
            target_lang="en",
            glossary=(GlossaryEntry(source="太郎", target="Taro"),),
        )
        assert len(first) == 1
        assert len(rerun) == 1  # injecting the glossary changed the context → recomputed


def _selected(unit: TranslationUnit) -> str:
    return next(c.text for c in unit.candidates if c.id == unit.selected_candidate_id)


def test_parallel_matches_serial_and_cache_still_skips(tmp_path: Path) -> None:
    def build(store: ProjectStore) -> None:
        for p in range(4):
            page = _page(store, index=p)
            region = _region(store, page, order=0, text=f"src{p}")
            _unit(store, page, [region])

    with _store(tmp_path / "serial") as serial:
        build(serial)
        translate_units(serial, translate=_echo, signature="fake@1", target_lang="en", jobs=1)
        serial_texts = {u.source_bundle: _selected(u) for u in serial.db.list(TranslationUnit)}

    with _store(tmp_path / "parallel") as parallel:
        build(parallel)
        updated = translate_units(
            parallel, translate=_echo, signature="fake@1", target_lang="en", jobs=4
        )
        assert len(updated) == 4
        parallel_texts = {u.source_bundle: _selected(u) for u in parallel.db.list(TranslationUnit)}
        assert parallel_texts == serial_texts
        # Unchanged pages skip even across workers (NFR-8).
        assert (
            translate_units(parallel, translate=_echo, signature="fake@1", target_lang="en", jobs=4)
            == []
        )
