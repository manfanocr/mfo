"""Read views and edit mutations behind the review editor (spec §13.2; FR-37/42/49; I-3).

This is the framework-free heart of the review backend: pure functions over a
:class:`~mfo.storage.project.ProjectStore` that (a) assemble the page-editor payloads the UI
shows — the page, its regions, their OCR, the translation units with full candidate and edit
history, and confidence (FR-42, §13.2) — and (b) apply the two edits review needs at this stage:
editing a unit's translation in place (FR-37) and re-selecting a prior candidate (FR-49).

Every mutation appends an immutable :class:`~mfo.core.models.EditRecord` (FR-42) and lands the
user's choice as a *selected* translation so it takes precedence over automation: the translate
stage preserves non-``RAW`` candidates and a selection pointing at one, so a later re-translation
never silently overwrites approved text (I-3). Keeping this layer HTTP-free means the review logic
is fully testable without a running server; :mod:`mfo.ui.server` is a thin FastAPI shell over it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mfo.core import (
    OCRSpan,
    Page,
    Region,
    TranslationCandidate,
    TranslationUnit,
    selected_text,
)
from mfo.core.confidence import DEFAULT_THRESHOLD, aggregate_confidence, is_low_confidence
from mfo.core.enums import CandidateKind, EditAction
from mfo.core.geometry import BBox
from mfo.storage.edits import list_edits, record_edit
from mfo.storage.project import ProjectStore


class NotFoundError(LookupError):
    """A requested entity (page, unit, candidate) does not exist in the project."""


# -- read views ---------------------------------------------------------------------------


def _bbox_payload(bbox: BBox) -> dict[str, float]:
    return {"x": bbox.x, "y": bbox.y, "width": bbox.width, "height": bbox.height}


def _ocr_payload(span: OCRSpan) -> dict[str, Any]:
    return {
        "span_id": span.id,
        "text": span.text,
        "confidence": span.confidence,
        "alternatives": list(span.alternatives),
    }


def _region_payload(region: Region, spans: list[OCRSpan]) -> dict[str, Any]:
    """One region with its OCR and aggregate confidence, for the page editor (§13.2)."""
    confidence = aggregate_confidence(region, spans)
    return {
        "region_id": region.id,
        "type": region.type.value,
        "reading_order_index": region.reading_order_index,
        "status": region.status.value,
        "confidence": confidence,
        "low_confidence": is_low_confidence(confidence),
        "bbox": _bbox_payload(region.bbox),
        "ocr": [_ocr_payload(span) for span in spans],
    }


def _candidate_payload(candidate: TranslationCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "kind": candidate.kind.value,
        "text": candidate.text,
        "confidence": candidate.confidence,
        "rationale": candidate.rationale,
    }


def _edit_payload(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "before": record.before,
        "after": record.after,
        "action": record.action.value,
        "editor": record.editor,
        "timestamp": record.timestamp.isoformat(),
    }


def _unit_payload(store: ProjectStore, unit: TranslationUnit) -> dict[str, Any]:
    """One translation unit: source, the selected translation, its candidates and edit history."""
    return {
        "unit_id": unit.id,
        "page_id": unit.page_id,
        "ordered_region_ids": list(unit.ordered_region_ids),
        "source_text": unit.source_bundle,
        "translation": selected_text(unit),
        "selected_candidate_id": unit.selected_candidate_id,
        "style": unit.style.value if unit.style is not None else None,
        "candidates": [_candidate_payload(c) for c in unit.candidates],
        "edits": [_edit_payload(record) for record in list_edits(store, unit.id)],
    }


def _require_page(store: ProjectStore, page_id: str) -> Page:
    page = store.db.get(Page, page_id)
    if page is None:
        raise NotFoundError(f"no page {page_id!r}")
    return page


def _require_unit(store: ProjectStore, unit_id: str) -> TranslationUnit:
    unit = store.db.get(TranslationUnit, unit_id)
    if unit is None:
        raise NotFoundError(f"no translation unit {unit_id!r}")
    return unit


def project_summary(store: ProjectStore, *, threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Project header plus a per-page index for the main screen (§13.1, FR-49 navigation)."""
    project = store.project
    spans = _spans_by_region(store)
    regions_by_page: dict[str, list[Region]] = {}
    for region in store.db.list(Region):
        regions_by_page.setdefault(region.page_id, []).append(region)
    units_by_page: dict[str, int] = {}
    for unit in store.db.list(TranslationUnit):
        units_by_page[unit.page_id] = units_by_page.get(unit.page_id, 0) + 1

    pages: list[dict[str, Any]] = []
    for page in store.db.list(Page, order_by="idx"):
        regions = regions_by_page.get(page.id, [])
        low = sum(
            1
            for region in regions
            if is_low_confidence(
                aggregate_confidence(region, spans.get(region.id, [])), threshold=threshold
            )
        )
        pages.append(
            {
                "page_id": page.id,
                "index": page.index,
                "image_path": page.image_path,
                "width": page.width,
                "height": page.height,
                "regions": len(regions),
                "units": units_by_page.get(page.id, 0),
                "low_confidence": low,
            }
        )

    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "source_lang": project.source_lang,
            "target_lang": project.target_lang,
            "reading_direction": project.reading_direction.value,
        },
        "pages": pages,
    }


def _spans_by_region(store: ProjectStore) -> dict[str, list[OCRSpan]]:
    by_region: dict[str, list[OCRSpan]] = {}
    for span in store.db.list(OCRSpan):
        by_region.setdefault(span.region_id, []).append(span)
    return by_region


def page_view(store: ProjectStore, page_id: str) -> dict[str, Any]:
    """The full page-editor payload: page, regions+OCR+confidence, and units (§13.2, FR-42)."""
    page = _require_page(store, page_id)
    regions = store.db.list(Region, where=("page_id", page.id))
    spans = {
        region.id: store.db.list(OCRSpan, where=("region_id", region.id)) for region in regions
    }
    ordered = sorted(
        regions,
        key=lambda r: (
            r.reading_order_index if r.reading_order_index is not None else 1 << 30,
            r.id,
        ),
    )
    units = store.db.list(TranslationUnit, where=("page_id", page.id))
    return {
        "page_id": page.id,
        "index": page.index,
        "image_path": page.image_path,
        "width": page.width,
        "height": page.height,
        "regions": [_region_payload(region, spans.get(region.id, [])) for region in ordered],
        "units": [_unit_payload(store, unit) for unit in sorted(units, key=lambda u: u.id)],
    }


def unit_view(store: ProjectStore, unit_id: str) -> dict[str, Any]:
    """A single translation unit with its candidates and edit history (FR-42, FR-49)."""
    return _unit_payload(store, _require_unit(store, unit_id))


def page_image_path(store: ProjectStore, page_id: str) -> Path:
    """Filesystem path of a page's source image, for the canvas (read-only, I-1)."""
    page = _require_page(store, page_id)
    return store.layout.root / page.image_path


# -- mutations ----------------------------------------------------------------------------


def edit_translation(
    store: ProjectStore, unit_id: str, text: str, *, editor: str = "user"
) -> dict[str, Any]:
    """Set a unit's translation to user-entered ``text`` in place (FR-37); records the edit.

    The text lands as a ``MANUAL`` candidate and becomes the selection, so automation preserves it
    (I-3). Repeated edits reuse the existing manual candidate rather than piling up duplicates; the
    machine candidates stay as alternatives the user can revert to.
    """
    unit = _require_unit(store, unit_id)
    before = selected_text(unit)

    selected = next((c for c in unit.candidates if c.id == unit.selected_candidate_id), None)
    if selected is not None and selected.kind is CandidateKind.MANUAL:
        candidates = [
            c.model_copy(update={"text": text}) if c.id == selected.id else c
            for c in unit.candidates
        ]
        selected_id = selected.id
    else:
        manual = TranslationCandidate(text=text, kind=CandidateKind.MANUAL)
        candidates = [*unit.candidates, manual]
        selected_id = manual.id

    updated = unit.model_copy(
        update={"candidates": candidates, "selected_candidate_id": selected_id}
    )
    store.db.save(updated)
    record_edit(
        store,
        unit_id=unit.id,
        before=before,
        after=text,
        action=EditAction.EDIT_TRANSLATION,
        editor=editor,
    )
    return _unit_payload(store, updated)


def select_candidate(
    store: ProjectStore, unit_id: str, candidate_id: str, *, editor: str = "user"
) -> dict[str, Any]:
    """Choose an existing candidate as the unit's translation (FR-49); records the edit.

    Lets the user revisit a prior decision — e.g. revert to a machine candidate — and keeps the
    choice as the selection so it survives re-translation (I-3).
    """
    unit = _require_unit(store, unit_id)
    candidate = next((c for c in unit.candidates if c.id == candidate_id), None)
    if candidate is None:
        raise NotFoundError(f"no candidate {candidate_id!r} on unit {unit_id!r}")
    before = selected_text(unit)

    updated = unit.model_copy(update={"selected_candidate_id": candidate.id})
    store.db.save(updated)
    record_edit(
        store,
        unit_id=unit.id,
        before=before,
        after=candidate.text,
        action=EditAction.SELECT_CANDIDATE,
        editor=editor,
    )
    return _unit_payload(store, updated)
