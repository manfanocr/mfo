"""Tests for the SFX storage stage: classify regions + attach transliterations (SG-5; I-3)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import OCRSpan, Page, Project, Region, TranslationCandidate, TranslationUnit
from mfo.core.enums import CandidateKind, RegionStatus, RegionType, SfxMode
from mfo.core.geometry import BBox
from mfo.core.traceability import selected_text
from mfo.storage import ProjectStore, classify_sfx_regions, process_sfx, transliterate_sfx_units


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def _page(store: ProjectStore) -> Page:
    page = Page(project_id=store.project.id, index=0, image_path="p0.png", width=200, height=300)
    store.db.save(page)
    return page


def _region(
    store: ProjectStore,
    page: Page,
    *,
    box: BBox,
    type: RegionType = RegionType.UNKNOWN,
    status: RegionStatus = RegionStatus.AUTO,
    text: str | None = None,
) -> Region:
    region = Region(page_id=page.id, bbox=box, type=type, status=status, reading_order_index=0)
    store.db.save(region)
    if text is not None:
        store.db.save(OCRSpan(region_id=region.id, text=text))
    return region


def _unit(store: ProjectStore, page: Page, region: Region) -> TranslationUnit:
    unit = TranslationUnit(page_id=page.id, ordered_region_ids=[region.id])
    store.db.save(unit)
    return unit


# Classify everything-or-nothing helpers, so the tests don't depend on the geometry thresholds.
def _always_sfx(region: Region, page: Page) -> RegionType:
    return RegionType.SFX if region.type is RegionType.UNKNOWN else region.type


def _romaji(_text: str) -> str:
    return "DOON"


def test_classify_promotes_unknown_auto_regions_only(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        unknown = _region(store, page, box=BBox(x=0, y=0, width=100, height=20))
        bubble = _region(
            store, page, box=BBox(x=0, y=30, width=40, height=40), type=RegionType.BUBBLE
        )
        manual = _region(
            store, page, box=BBox(x=0, y=80, width=100, height=20), status=RegionStatus.MANUAL
        )

        changed = classify_sfx_regions(store, classify=_always_sfx)

        assert [r.id for r in changed] == [unknown.id]
        by_id = {r.id: r for r in store.db.list(Region)}
        assert by_id[unknown.id].type is RegionType.SFX  # promoted
        assert by_id[bubble.id].type is RegionType.BUBBLE  # detector label kept
        assert by_id[manual.id].type is RegionType.UNKNOWN  # human-touched region untouched (I-3)


def test_transliterate_attaches_sfx_candidate_to_sfx_units(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        sfx = _region(
            store,
            page,
            box=BBox(x=0, y=0, width=100, height=20),
            type=RegionType.SFX,
            text="ドーン",
        )
        dialogue = _region(
            store,
            page,
            box=BBox(x=0, y=30, width=40, height=40),
            type=RegionType.BUBBLE,
            text="こんにちは",
        )
        sfx_unit = _unit(store, page, sfx)
        dialogue_unit = _unit(store, page, dialogue)

        changed = transliterate_sfx_units(store, transliterate=_romaji, mode=SfxMode.TRANSLITERATE)

        assert [u.id for u in changed] == [sfx_unit.id]  # only the SFX-led unit
        updated = {u.id: u for u in store.db.list(TranslationUnit)}
        sfx_now = updated[sfx_unit.id]
        assert any(c.kind is CandidateKind.SFX and c.text == "DOON" for c in sfx_now.candidates)
        # transliterate mode selects the SFX candidate.
        assert selected_text(sfx_now) == "DOON"
        # The dialogue unit is untouched.
        assert updated[dialogue_unit.id].candidates == []


def test_render_mode_creates_candidate_but_does_not_select_it(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        sfx = _region(
            store,
            page,
            box=BBox(x=0, y=0, width=100, height=20),
            type=RegionType.SFX,
            text="ドーン",
        )
        # The unit already has a machine translation selected.
        raw = TranslationCandidate(text="boom", kind=CandidateKind.RAW)
        unit = TranslationUnit(
            page_id=page.id,
            ordered_region_ids=[sfx.id],
            candidates=[raw],
            selected_candidate_id=raw.id,
        )
        store.db.save(unit)

        transliterate_sfx_units(store, transliterate=_romaji, mode=SfxMode.RENDER)

        updated = store.db.list(TranslationUnit)[0]
        # The SFX candidate is available for review, but render mode keeps the existing selection.
        assert any(c.kind is CandidateKind.SFX for c in updated.candidates)
        assert selected_text(updated) == "boom"


def test_transliterate_never_overrides_a_human_selection(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        sfx = _region(
            store,
            page,
            box=BBox(x=0, y=0, width=100, height=20),
            type=RegionType.SFX,
            text="ドーン",
        )
        manual = TranslationCandidate(text="KABOOM", kind=CandidateKind.MANUAL)
        unit = TranslationUnit(
            page_id=page.id,
            ordered_region_ids=[sfx.id],
            candidates=[manual],
            selected_candidate_id=manual.id,
        )
        store.db.save(unit)

        transliterate_sfx_units(store, transliterate=_romaji, mode=SfxMode.TRANSLITERATE)

        # Even in transliterate mode, the human's choice wins (I-3).
        assert selected_text(store.db.list(TranslationUnit)[0]) == "KABOOM"


def test_process_sfx_classifies_then_transliterates(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        region = _region(store, page, box=BBox(x=0, y=0, width=100, height=20), text="ドーン")
        unit = _unit(store, page, region)

        result = process_sfx(
            store, classify=_always_sfx, transliterate=_romaji, mode=SfxMode.TRANSLITERATE
        )

        assert [r.id for r in result.classified] == [region.id]
        assert [u.id for u in result.transliterated] == [unit.id]
        assert selected_text(store.db.list(TranslationUnit)[0]) == "DOON"
