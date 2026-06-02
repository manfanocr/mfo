"""Persist context-aware translations per unit (spec §10.6, §12.5; FR-21, FR-22; I-2, I-3, NFR-8).

The translation callable is *injected* (the language layer supplies it) so storage stays free of any
provider dependency, mirroring the OCR/detect stages. For each page this assembles every unit's
``source_bundle`` from its regions' OCR spans in reading order — the text grouping deliberately left
empty — builds each unit's ``context_bundle`` from its neighbours and page locator
(:func:`mfo.core.context.build_context`), translates it, and stores the result as a
``TranslationCandidate`` on the unit (kept separate from the OCR source, FR-15).

Each page records a translation signature folding the translator id, the target language, and a
fingerprint of its units (ids, region links, source text, context). Re-running skips unchanged pages
(NFR-8); a re-OCR or re-grouping changes the fingerprint and invalidates it. A recompute only
replaces this stage's own machine output: any human (or AI) candidate, and a human selection that
points at one, is preserved — automation never silently overwrites approved text (I-3).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from typing import Any, Protocol

from mfo.core import OCRSpan, Page, Region, TranslationCandidate, TranslationUnit
from mfo.core.context import DEFAULT_NEIGHBOR_WINDOW, build_context
from mfo.core.enums import CandidateKind
from mfo.storage.hashing import content_key
from mfo.storage.project import ProjectStore

# How the text of a unit's regions is joined into its source bundle.
_SOURCE_JOIN = "\n"


class Translated(Protocol):
    """The minimum a translation result must expose to be persisted."""

    @property
    def text(self) -> str: ...

    @property
    def confidence(self) -> float | None: ...


Translate = Callable[[str, dict[str, Any]], Translated]


def _order_key(unit: TranslationUnit, order_by_region: dict[str, float]) -> float:
    """Reading-order rank of a unit, taken from its first region (unplaced units sort last)."""
    if unit.ordered_region_ids:
        return order_by_region.get(unit.ordered_region_ids[0], math.inf)
    return math.inf


def _assemble_source(unit: TranslationUnit, text_by_region: dict[str, str]) -> str:
    """Join the OCR text of a unit's regions, in reading order, into its source bundle."""
    parts = [text_by_region[rid] for rid in unit.ordered_region_ids if text_by_region.get(rid)]
    return _SOURCE_JOIN.join(parts)


def _units_fingerprint(
    units: list[TranslationUnit],
    sources: list[str],
    contexts: list[dict[str, Any]],
) -> str:
    """A stable digest of a page's units for translation, so re-OCR/re-grouping invalidates it."""
    digest = hashlib.sha256()
    for unit, source, context in sorted(
        zip(units, sources, contexts, strict=True), key=lambda item: item[0].id
    ):
        digest.update(f"{unit.id}:{','.join(unit.ordered_region_ids)}:{source}:".encode())
        digest.update(json.dumps(context, sort_keys=True, ensure_ascii=False).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _apply_translation(
    unit: TranslationUnit,
    source: str,
    context: dict[str, Any],
    result: Translated,
) -> TranslationUnit:
    """Attach a fresh machine candidate, preserving any human/AI candidate and selection (I-3)."""
    preserved = [c for c in unit.candidates if c.kind is not CandidateKind.RAW]
    raw = TranslationCandidate(
        text=result.text, kind=CandidateKind.RAW, confidence=result.confidence
    )
    preserved_ids = {c.id for c in preserved}
    # Keep a human selection if it points at a preserved candidate; else select the new machine one.
    selected = unit.selected_candidate_id if unit.selected_candidate_id in preserved_ids else raw.id
    return unit.model_copy(
        update={
            "source_bundle": source,
            "context_bundle": context,
            "candidates": [*preserved, raw],
            "selected_candidate_id": selected,
        }
    )


def translate_units(
    store: ProjectStore,
    *,
    translate: Translate,
    signature: str,
    target_lang: str,
    window: int = DEFAULT_NEIGHBOR_WINDOW,
    force: bool = False,
) -> list[TranslationUnit]:
    """Translate every page's units with context; returns the units (re)translated."""
    updated: list[TranslationUnit] = []
    pages = store.db.list(Page, order_by="idx")
    page_count = len(pages)
    for page in pages:
        units = store.db.list(TranslationUnit, where=("page_id", page.id))
        if not units:
            continue

        regions = store.db.list(Region, where=("page_id", page.id))
        order_by_region = {
            region.id: (
                float(region.reading_order_index)
                if region.reading_order_index is not None
                else math.inf
            )
            for region in regions
        }
        text_by_region: dict[str, str] = {}
        for region in regions:
            spans = store.db.list(OCRSpan, where=("region_id", region.id))
            if spans:
                text_by_region[region.id] = spans[0].text

        units = sorted(units, key=lambda u: _order_key(u, order_by_region))
        sources = [_assemble_source(unit, text_by_region) for unit in units]
        contexts = [
            build_context(
                sources, index, page_index=page.index, page_count=page_count, window=window
            )
            for index in range(len(units))
        ]

        page_signature = content_key(
            f"translate|{signature}|{target_lang}|{window}",
            _units_fingerprint(units, sources, contexts),
        )
        if not force and page.translation.get("signature") == page_signature:
            continue

        new_units = [
            _apply_translation(unit, source, context, translate(source, context))
            for unit, source, context in zip(units, sources, contexts, strict=True)
        ]
        store.db.save_all(new_units)
        store.db.save(
            page.model_copy(
                update={
                    "translation": {
                        "signature": page_signature,
                        "translator": signature,
                        "target_lang": target_lang,
                        "count": len(new_units),
                    }
                }
            )
        )
        updated.extend(new_units)
    return updated
