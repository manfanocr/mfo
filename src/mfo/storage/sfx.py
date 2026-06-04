"""SFX handling: classify SFX regions and attach transliterations (spec §10.6, §10.8; SG-5; I-3).

This stage handles sound-effect regions distinctly from dialogue (SG-5). It does two things, both
idempotent and offline-by-default:

1. **Classify** — promote otherwise-:attr:`~mfo.core.enums.RegionType.UNKNOWN` regions to
   :attr:`~mfo.core.enums.RegionType.SFX` using an injected classifier (the geometry baseline, or a
   plugin). Only auto, untyped regions are touched — a detector's label or a human edit wins (I-3).
2. **Transliterate** — for every unit that leads with an SFX region, attach a transliteration as an
   :attr:`~mfo.core.enums.CandidateKind.SFX` candidate (injected transliterator). The candidate is
   always produced so it is available in review; whether it is *selected* depends on the project's
   :class:`~mfo.core.enums.SfxMode` — only ``transliterate`` selects it, and never over a human
   (MANUAL) choice (I-3).

The classifier and transliterator are *injected* (the vision/language layers supply them) so storage
stays free of any provider dependency, mirroring the OCR/translate stages. The render-time toggle
(render / transliterate / skip) is honoured separately by the mask/composite stages, which leave SFX
regions unmasked/unset when the mode is ``skip``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mfo.core import OCRSpan, Page, Region, TranslationCandidate, TranslationUnit
from mfo.core.enums import CandidateKind, RegionStatus, RegionType, SfxMode
from mfo.storage.project import ProjectStore

# A classifier decides a region's type from its box + page size; a transliterator romanizes text.
ClassifyType = Callable[[Region, Page], RegionType]
Transliterate = Callable[[str], str]


@dataclass(frozen=True)
class SfxResult:
    """What an SFX pass changed: regions retyped to SFX and units given a transliteration."""

    classified: list[Region]
    transliterated: list[TranslationUnit]


def classify_sfx_regions(store: ProjectStore, *, classify: ClassifyType) -> list[Region]:
    """Promote auto, untyped regions the classifier calls SFX; returns the regions changed (I-3).

    Only :attr:`RegionStatus.AUTO` regions still typed :attr:`RegionType.UNKNOWN` are eligible, so a
    detector's type and any human edit are kept. Idempotent: a region already typed SFX is left.
    """
    pages = {page.id: page for page in store.db.list(Page)}
    changed: list[Region] = []
    for region in store.db.list(Region):
        if region.status is not RegionStatus.AUTO or region.type is not RegionType.UNKNOWN:
            continue
        page = pages.get(region.page_id)
        if page is None:
            continue
        new_type = classify(region, page)
        if new_type is not region.type:
            updated = region.model_copy(update={"type": new_type})
            store.db.save(updated)
            changed.append(updated)
    return changed


def _lead_region_type(unit: TranslationUnit, regions: dict[str, Region]) -> RegionType | None:
    """The type of a unit's first (reading-order) region, or ``None`` if it has none."""
    for region_id in unit.ordered_region_ids:
        region = regions.get(region_id)
        if region is not None:
            return region.type
    return None


def _unit_source(unit: TranslationUnit, text_by_region: dict[str, str]) -> str:
    """Join the OCR text of a unit's regions in reading order (the unit's source text)."""
    parts = [text_by_region[rid] for rid in unit.ordered_region_ids if text_by_region.get(rid)]
    return "\n".join(parts)


def _apply_sfx_candidate(unit: TranslationUnit, *, text: str, mode: SfxMode) -> TranslationUnit:
    """Attach/refresh the SFX candidate, selecting it only in ``transliterate`` mode (I-3).

    Any existing non-SFX candidate (machine, AI, or human) is preserved; a prior SFX candidate is
    replaced. The selection moves to the SFX candidate only when the mode is ``transliterate`` and
    the current selection is not a human (MANUAL) choice — automation never overrides approved text.
    """
    preserved = [c for c in unit.candidates if c.kind is not CandidateKind.SFX]
    sfx = TranslationCandidate(text=text, kind=CandidateKind.SFX)
    candidates = [*preserved, sfx]

    selected = unit.selected_candidate_id
    if selected not in {c.id for c in candidates}:
        selected = None
    if mode is SfxMode.TRANSLITERATE:
        current = next((c for c in unit.candidates if c.id == unit.selected_candidate_id), None)
        if current is None or current.kind is not CandidateKind.MANUAL:
            selected = sfx.id
    return unit.model_copy(update={"candidates": candidates, "selected_candidate_id": selected})


def transliterate_sfx_units(
    store: ProjectStore, *, transliterate: Transliterate, mode: SfxMode
) -> list[TranslationUnit]:
    """Attach a transliteration candidate to every SFX-led unit; returns the units changed.

    Units that don't lead with an SFX region, or have no OCR text, are left untouched so dialogue
    handling is unchanged.
    """
    updated: list[TranslationUnit] = []
    for page in store.db.list(Page, order_by="idx"):
        units = store.db.list(TranslationUnit, where=("page_id", page.id))
        if not units:
            continue
        regions = {r.id: r for r in store.db.list(Region, where=("page_id", page.id))}
        text_by_region: dict[str, str] = {}
        for region in regions.values():
            spans = store.db.list(OCRSpan, where=("region_id", region.id))
            if spans:
                text_by_region[region.id] = spans[0].text

        for unit in units:
            if _lead_region_type(unit, regions) is not RegionType.SFX:
                continue
            source = _unit_source(unit, text_by_region)
            if not source.strip():
                continue
            new_unit = _apply_sfx_candidate(unit, text=transliterate(source), mode=mode)
            store.db.save(new_unit)
            updated.append(new_unit)
    return updated


def process_sfx(
    store: ProjectStore,
    *,
    classify: ClassifyType,
    transliterate: Transliterate,
    mode: SfxMode,
) -> SfxResult:
    """Run the full SFX pass: classify regions, then transliterate the SFX-led units (SG-5)."""
    classified = classify_sfx_regions(store, classify=classify)
    transliterated = transliterate_sfx_units(store, transliterate=transliterate, mode=mode)
    return SfxResult(classified=classified, transliterated=transliterated)
