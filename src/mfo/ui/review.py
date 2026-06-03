"""Read views and edit mutations behind the review editor (spec §13.2/13.3/13.4; FR-37/42/49; I-3).

This is the framework-free heart of the review backend: pure functions over a
:class:`~mfo.storage.project.ProjectStore` that (a) assemble the page-editor payloads the UI
shows — the page, its regions, their OCR, the translation units with full candidate and edit
history, and confidence (FR-42, §13.2) — and (b) apply the edits review needs: editing a unit's
translation in place (FR-37), re-selecting a prior candidate (FR-49), the region operations of
§13.3/13.4 — set status (FR-40), move/resize a region (FR-38), manual reading-order correction
(FR-20), and split/merge regions (FR-39) — plus the low-confidence-first review queue and a
single-page re-render preview so edits become visible immediately.

Translation mutations append an immutable :class:`~mfo.core.models.EditRecord` (FR-42) and land the
user's choice as a *selected* translation so it takes precedence over automation: the translate
stage preserves non-``RAW`` candidates and a selection pointing at one, so a later re-translation
never silently overwrites approved text (I-3). Region operations persist directly on the
:class:`~mfo.core.models.Region`/:class:`~mfo.core.models.TranslationUnit` rows so they survive
re-open. Keeping this layer HTTP-free means the review logic is fully testable without a running
server; :mod:`mfo.ui.server` is a thin FastAPI shell over it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mfo.core import (
    OCRSpan,
    Page,
    Region,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
    selected_text,
)
from mfo.core.confidence import DEFAULT_THRESHOLD, aggregate_confidence, is_low_confidence
from mfo.core.context import DEFAULT_NEIGHBOR_WINDOW, build_context
from mfo.core.enums import CandidateKind, EditAction, RegionStatus, RegionType, TranslationStyle
from mfo.core.geometry import BBox
from mfo.core.glossary import (
    GlossaryEntry,
    applicable_entries,
    apply_glossary,
    glossary_terms,
)
from mfo.render import (
    CompositeArtifact,
    MaskConfig,
    Placement,
    composite_file,
    get_preset,
    mask_file,
)
from mfo.storage import RENDER_KIND, composite_pages, mask_pages
from mfo.storage.edits import list_edits, record_edit
from mfo.storage.ocr import Recognize
from mfo.storage.project import ProjectStore
from mfo.storage.render import PagePlacement
from mfo.storage.translate import Translate

# A reading-order index sentinel that sorts unordered regions last, mirroring page_view's ordering.
_ORDER_LAST = 1 << 30


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


def _require_region(store: ProjectStore, region_id: str) -> Region:
    region = store.db.get(Region, region_id)
    if region is None:
        raise NotFoundError(f"no region {region_id!r}")
    return region


def _order_key(region: Region) -> tuple[int, str]:
    """Reading-order sort key (unordered regions sort last, then by id for determinism)."""
    index = region.reading_order_index
    return (index if index is not None else _ORDER_LAST, region.id)


def _page_regions_in_order(store: ProjectStore, page_id: str) -> list[Region]:
    return sorted(store.db.list(Region, where=("page_id", page_id)), key=_order_key)


def _reindex(store: ProjectStore, ordered: list[Region]) -> None:
    """Persist a contiguous 0..n-1 reading order across a page's regions (only what changed)."""
    for index, region in enumerate(ordered):
        if region.reading_order_index != index:
            store.db.save(region.model_copy(update={"reading_order_index": index}))


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
    ordered = sorted(regions, key=_order_key)
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


# -- region operations (§13.3/13.4; FR-20/38/39/40) ---------------------------------------
#
# These persist directly on the Region/TranslationUnit rows (not as EditRecords, which are unit
# scoped) and return the refreshed page view so the editor can redraw in one round-trip. Each op
# that changes geometry leaves the page's reading order contiguous so re-render and export stay
# deterministic; a later re-render picks the change up via its placement signature (NFR-8).


def set_region_status(store: ProjectStore, region_id: str, status: str) -> dict[str, Any]:
    """Flag a region correct / needs-review / ignore / manual (FR-40); a user edit wins (I-3).

    The flag persists on the region, so confidence-driven auto-flagging (which only touches ``AUTO``
    regions) never clobbers it on a later run.
    """
    region = _require_region(store, region_id)
    try:
        new_status = RegionStatus(status)
    except ValueError:
        allowed = ", ".join(s.value for s in RegionStatus)
        raise ValueError(f"unknown region status {status!r}; allowed: {allowed}") from None
    store.db.save(region.model_copy(update={"status": new_status}))
    return page_view(store, region.page_id)


def move_region(
    store: ProjectStore, region_id: str, *, x: float, y: float, width: float, height: float
) -> dict[str, Any]:
    """Reposition and resize a region's bounding box (FR-38). Re-render reflows text into it."""
    region = _require_region(store, region_id)
    if width < 0 or height < 0:
        raise ValueError("region width and height must be non-negative")
    bbox = BBox(x=x, y=y, width=width, height=height)
    store.db.save(region.model_copy(update={"bbox": bbox}))
    return page_view(store, region.page_id)


def reorder_regions(
    store: ProjectStore, page_id: str, ordered_region_ids: list[str]
) -> dict[str, Any]:
    """Apply a manual reading-order correction to a page (FR-20).

    ``ordered_region_ids`` must be a permutation of the page's region ids; each region's
    ``reading_order_index`` is set to its position in that list.
    """
    _require_page(store, page_id)
    by_id = {region.id: region for region in store.db.list(Region, where=("page_id", page_id))}
    if set(ordered_region_ids) != set(by_id):
        raise ValueError("ordered_region_ids must be a permutation of the page's regions")
    _reindex(store, [by_id[rid] for rid in ordered_region_ids])
    return page_view(store, page_id)


def split_region(
    store: ProjectStore, region_id: str, *, orientation: str = "horizontal", ratio: float = 0.5
) -> dict[str, Any]:
    """Split a region into two adjacent regions (FR-39).

    ``orientation`` ``"horizontal"`` cuts top/bottom, ``"vertical"`` cuts left/right, at ``ratio``
    of the box. The original keeps the first piece and its OCR; a new region takes the second piece
    and is inserted right after the original in reading order and in any unit that contained it.
    """
    if not 0.0 < ratio < 1.0:
        raise ValueError("split ratio must be between 0 and 1 (exclusive)")
    if orientation not in ("horizontal", "vertical"):
        raise ValueError("orientation must be 'horizontal' or 'vertical'")
    region = _require_region(store, region_id)
    b = region.bbox
    if orientation == "horizontal":
        cut = b.height * ratio
        first = BBox(x=b.x, y=b.y, width=b.width, height=cut)
        second = BBox(x=b.x, y=b.y + cut, width=b.width, height=b.height - cut)
    else:
        cut = b.width * ratio
        first = BBox(x=b.x, y=b.y, width=cut, height=b.height)
        second = BBox(x=b.x + cut, y=b.y, width=b.width - cut, height=b.height)

    new_region = Region(page_id=region.page_id, bbox=second, type=region.type, status=region.status)
    store.db.save(region.model_copy(update={"bbox": first}))
    store.db.save(new_region)

    ordered = [r for r in _page_regions_in_order(store, region.page_id) if r.id != new_region.id]
    position = next(i for i, r in enumerate(ordered) if r.id == region.id)
    ordered.insert(position + 1, new_region)
    _reindex(store, ordered)

    for unit in store.db.list(TranslationUnit, where=("page_id", region.page_id)):
        if region.id in unit.ordered_region_ids:
            ids = list(unit.ordered_region_ids)
            ids.insert(ids.index(region.id) + 1, new_region.id)
            store.db.save(unit.model_copy(update={"ordered_region_ids": ids}))

    return page_view(store, region.page_id)


def merge_regions(store: ProjectStore, region_ids: list[str]) -> dict[str, Any]:
    """Merge two or more regions on the same page into one (FR-39).

    The earliest region in reading order survives (keeping its id, type and status); its box grows
    to the union, the others' OCR moves onto it so no transcription is lost (I-2), the others are
    deleted, and every unit that referenced a merged region now references the survivor (order
    preserved, duplicates collapsed).
    """
    if len(region_ids) < 2:
        raise ValueError("merging needs at least two regions")
    regions = [_require_region(store, rid) for rid in region_ids]
    page_ids = {region.page_id for region in regions}
    if len(page_ids) != 1:
        raise ValueError("can only merge regions on the same page")
    page_id = page_ids.pop()

    survivor = min(regions, key=_order_key)
    others = [region for region in regions if region.id != survivor.id]
    merged_ids = {region.id for region in others}

    for other in others:
        for span in store.db.list(OCRSpan, where=("region_id", other.id)):
            store.db.save(span.model_copy(update={"region_id": survivor.id}))
    store.db.save(survivor.model_copy(update={"bbox": BBox.union([r.bbox for r in regions])}))
    for other in others:
        store.db.delete(Region, where=("id", other.id))

    for unit in store.db.list(TranslationUnit, where=("page_id", page_id)):
        new_ids: list[str] = []
        for rid in unit.ordered_region_ids:
            mapped = survivor.id if rid in merged_ids else rid
            if mapped not in new_ids:
                new_ids.append(mapped)
        if new_ids != list(unit.ordered_region_ids):
            store.db.save(unit.model_copy(update={"ordered_region_ids": new_ids}))

    _reindex(store, _page_regions_in_order(store, page_id))
    return page_view(store, page_id)


def delete_region(store: ProjectStore, region_id: str) -> dict[str, Any]:
    """Delete a region the detector got wrong (FR-38/39); drops its OCR and detaches it from units.

    A unit left with no regions is removed too. The page's reading order is left contiguous so a
    later re-render/export stays deterministic.
    """
    region = _require_region(store, region_id)
    page_id = region.page_id
    store.db.delete(OCRSpan, where=("region_id", region.id))
    for unit in store.db.list(TranslationUnit, where=("page_id", page_id)):
        if region.id not in unit.ordered_region_ids:
            continue
        remaining = [rid for rid in unit.ordered_region_ids if rid != region.id]
        if remaining:
            store.db.save(unit.model_copy(update={"ordered_region_ids": remaining}))
        else:
            store.db.delete(TranslationUnit, where=("id", unit.id))
    store.db.delete(Region, where=("id", region.id))
    _reindex(store, _page_regions_in_order(store, page_id))
    return page_view(store, page_id)


def create_region(
    store: ProjectStore,
    page_id: str,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    recognize: Recognize,
    translate: Translate,
    target_lang: str,
    region_type: RegionType = RegionType.BUBBLE,
    style: TranslationStyle = TranslationStyle.BALANCED,
    glossary: Sequence[GlossaryEntry] = (),
    window: int = DEFAULT_NEIGHBOR_WINDOW,
) -> dict[str, Any]:
    """Add a user-drawn region, OCR its crop, and translate it immediately (FR-38; §13.3).

    The recognition and translation callables are injected (the server wires the project's engines),
    keeping this layer provider-free and unit-testable offline. The region is created ``MANUAL`` so
    automation never clobbers it (I-3); its OCR and the resulting machine candidate are stored just
    like the pipeline's, so the new unit is indistinguishable from a detected one downstream (I-2).
    """
    page = _require_page(store, page_id)
    if width <= 0 or height <= 0:
        raise ValueError("region width and height must be positive")
    bbox = BBox(x=x, y=y, width=width, height=height)
    region = Region(page_id=page_id, bbox=bbox, type=region_type, status=RegionStatus.MANUAL)
    store.db.save(region)
    _reindex(store, _page_regions_in_order(store, page_id))  # the new box sorts last; pin its index

    result = recognize(store.layout.root / page.image_path, bbox)
    store.db.save(
        OCRSpan(
            region_id=region.id,
            text=result.text,
            confidence=result.confidence,
            alternatives=list(result.alternatives),
        )
    )

    source = result.text
    page_count = len(store.db.list(Page))
    context = build_context(
        [source], 0, page_index=page.index, page_count=page_count, window=window
    )
    terms = glossary_terms(applicable_entries(source, glossary))
    if terms:
        context = {**context, "glossary": terms}
    translated = translate(source, context)
    candidate = TranslationCandidate(
        text=apply_glossary(translated.text, source, glossary),
        kind=CandidateKind.RAW,
        confidence=translated.confidence,
    )
    store.db.save(
        TranslationUnit(
            page_id=page_id,
            ordered_region_ids=[region.id],
            source_bundle=source,
            context_bundle=context,
            candidates=[candidate],
            selected_candidate_id=candidate.id,
            style=style,
        )
    )
    return page_view(store, page_id)


# -- review queue (§13.4) -----------------------------------------------------------------


def review_queue(store: ProjectStore, *, threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Order every region for review with low-confidence ones first (§13.4, I-4).

    Within the low-confidence group (and the rest), regions stay in page then reading order, so the
    editor can step a reviewer straight through the work that needs attention most.
    """
    spans = _spans_by_region(store)
    entries: list[dict[str, Any]] = []
    for page in store.db.list(Page, order_by="idx"):
        for region in _page_regions_in_order(store, page.id):
            # Auto-ignored panel/frame blobs aren't review work; keep them out of the queue.
            if region.status is RegionStatus.IGNORE:
                continue
            confidence = aggregate_confidence(region, spans.get(region.id, []))
            entries.append(
                {
                    "page_id": page.id,
                    "page_index": page.index,
                    "region_id": region.id,
                    "reading_order_index": region.reading_order_index,
                    "confidence": confidence,
                    "low_confidence": is_low_confidence(confidence, threshold=threshold),
                    "status": region.status.value,
                }
            )
    entries.sort(
        key=lambda e: (
            not e["low_confidence"],
            e["page_index"],
            e["reading_order_index"] if e["reading_order_index"] is not None else _ORDER_LAST,
        )
    )
    return {"threshold": threshold, "entries": entries}


# -- re-render preview (§13.3) ------------------------------------------------------------
#
# Mirror the CLI's compositing signature so the review preview and `mfo export` share one render
# cache: an unchanged page is a no-op, while an edit (new text or moved box) invalidates only the
# pages it touched (NFR-8).
COMPOSITE_SIGNATURE = "composite@1"


def _composite_adapter(base_path: Path, placements: list[PagePlacement]) -> CompositeArtifact:
    """Bind storage's placement data to the render compositor (the same wiring the CLI uses)."""
    return composite_file(
        base_path,
        [Placement(text=p.text, box=p.bbox, preset=get_preset(p.preset)) for p in placements],
    )


def _render_artifact(store: ProjectStore, page_id: str) -> RenderArtifact | None:
    for artifact in store.db.list(RenderArtifact, where=("page_id", page_id)):
        if artifact.params.get("kind") == RENDER_KIND:
            return artifact
    return None


def _render_payload(store: ProjectStore, page_id: str) -> dict[str, Any]:
    artifact = _render_artifact(store, page_id)
    if artifact is None:
        return {"page_id": page_id, "rendered": False, "render_url": None, "overflow": 0}
    return {
        "page_id": page_id,
        "rendered": True,
        "render_url": f"/api/pages/{page_id}/render",
        "overflow": int(artifact.params.get("overflow", 0)),
    }


def rerender_page(store: ProjectStore, page_id: str) -> dict[str, Any]:
    """Re-mask and re-composite so the edited page can be previewed (§13.3; FR-34/35).

    Runs the offline mask + composite stages with the project's saved masking config; their
    signatures mean only pages whose regions or selected text changed are actually recomputed.
    """
    _require_page(store, page_id)
    config = MaskConfig(**store.project.config.get("render", {}))
    mask_pages(
        store,
        mask=lambda image_path, boxes: mask_file(image_path, boxes, config),
        signature=config.signature(),
    )
    composite_pages(store, composite=_composite_adapter, signature=COMPOSITE_SIGNATURE)
    return _render_payload(store, page_id)


def page_render_path(store: ProjectStore, page_id: str) -> Path:
    """Filesystem path of a page's composited render, or raise if it has not been rendered yet."""
    _require_page(store, page_id)
    artifact = _render_artifact(store, page_id)
    if artifact is None:
        raise NotFoundError(f"no render for page {page_id!r}; render it first")
    return store.layout.root / artifact.output_path
