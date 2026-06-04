"""Core entity models for an mfo project (spec §11).

These models are the stable, explicit backbone of the data model (NFR-30) and carry the
traceability links from source image through OCR, translation, edits, and render
(invariants I-2, I-6). They are pure data containers with no I/O; persistence lives in the
storage layer (batch 0.3).

Every entity has an immutable, self-describing :func:`~mfo.core.ids.new_id` identifier. All
models forbid unknown fields so typos and schema drift fail loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mfo.core.enums import (
    CandidateKind,
    EditAction,
    ReadingDirection,
    RegionStatus,
    RegionType,
    TranslationStyle,
)
from mfo.core.geometry import BBox, Point
from mfo.core.ids import new_id


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class MfoModel(BaseModel):
    """Base model: a stable, self-describing ``id`` and strict (no unknown) fields.

    Subclasses override ``id`` with an entity-specific prefixed default factory.
    """

    model_config = ConfigDict(extra="forbid")

    id: str


class Project(MfoModel):
    """Top-level container for a translation project."""

    id: str = Field(default_factory=partial(new_id, "prj"))
    name: str
    source_lang: str
    target_lang: str
    reading_direction: ReadingDirection = ReadingDirection.RTL
    created_at: datetime = Field(default_factory=_utcnow)
    config: dict[str, Any] = Field(default_factory=dict)
    # Backend/model versions used, for reproducibility (NFR-26, NFR-27).
    model_versions: dict[str, str] = Field(default_factory=dict)


class Page(MfoModel):
    """A single source page image. The original is never modified (invariant I-1)."""

    id: str = Field(default_factory=partial(new_id, "pg"))
    project_id: str
    index: int
    image_path: str  # relative to the project directory
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    preprocessing: dict[str, Any] = Field(default_factory=dict)
    # Detection provenance for this page (detector id + input signature), for cache/skip (NFR-8).
    detection: dict[str, Any] = Field(default_factory=dict)
    # OCR provenance for this page (engine id + input/regions signature), for cache/skip (NFR-8).
    ocr: dict[str, Any] = Field(default_factory=dict)
    # Reading-order provenance (direction + regions signature), for cache/skip (NFR-8).
    structure: dict[str, Any] = Field(default_factory=dict)
    # Dialogue-grouping provenance (params + regions signature), for cache/skip (NFR-8).
    grouping: dict[str, Any] = Field(default_factory=dict)
    # Translation provenance (translator id + target lang + units sig), for cache/skip (NFR-8).
    translation: dict[str, Any] = Field(default_factory=dict)
    # AI-assist provenance (assistant id + mode + units sig), for cache/skip (NFR-8); empty until
    # the optional AI layer runs (I-7).
    assist: dict[str, Any] = Field(default_factory=dict)
    # LLM OCR-correction provenance (corrector id + threshold + spans sig), for cache/skip (NFR-8);
    # empty until the optional, opt-in OCR-correction layer runs (SG-7, I-7).
    ocr_correction: dict[str, Any] = Field(default_factory=dict)
    # Optimistic-concurrency revision for collaborative review (SG-8/SG-10): bumped on every
    # committed review mutation and on undo/redo so a stale write from another reviewer can be
    # detected and rejected rather than silently lost. Monotonic and never reverted by undo.
    review_rev: int = Field(default=0, ge=0)


class Region(MfoModel):
    """A detected text region on a page."""

    id: str = Field(default_factory=partial(new_id, "rgn"))
    page_id: str
    bbox: BBox
    polygon: list[Point] | None = None
    type: RegionType = RegionType.UNKNOWN
    reading_order_index: int | None = None
    # Index of the panel/frame this region sits in, assigned by the panel-aware reading-order stage
    # (FR-18); ``None`` on the flat path or for regions outside every panel. Lets the translation
    # context window stay within a panel (SG-1) without merging units.
    panel_index: int | None = None
    confidence: float | None = None
    status: RegionStatus = RegionStatus.AUTO


class OCRSpan(MfoModel):
    """OCR output for a region, stored separately from translation (spec FR-15)."""

    id: str = Field(default_factory=partial(new_id, "ocr"))
    region_id: str
    text: str
    confidence: float | None = None
    alternatives: list[str] = Field(default_factory=list)
    token_offsets: list[tuple[int, int]] | None = None
    # Provenance: the engine/detector id that produced this span (I-2). Empty for legacy spans; set
    # to the detector signature when OCR is captured during detection (a det+rec engine), so the OCR
    # stage can tell detection-provided text from an OCR-stage run and adopt it (batch 8.0).
    source: str = ""


class TranslationCandidate(MfoModel):
    """One proposed translation for a unit (spec §12.3)."""

    id: str = Field(default_factory=partial(new_id, "cand"))
    text: str
    kind: CandidateKind = CandidateKind.RAW
    confidence: float | None = None
    rationale: str | None = None


class TranslationUnit(MfoModel):
    """A logical dialogue unit grouping one or more regions (spec FR-19)."""

    id: str = Field(default_factory=partial(new_id, "tu"))
    # The page this unit belongs to, for the source → unit link graph (I-2) and per-page recompute.
    page_id: str = ""
    ordered_region_ids: list[str] = Field(default_factory=list)
    source_bundle: str = ""
    context_bundle: dict[str, Any] = Field(default_factory=dict)
    candidates: list[TranslationCandidate] = Field(default_factory=list)
    selected_candidate_id: str | None = None
    style: TranslationStyle | None = None

    @model_validator(mode="after")
    def _selected_candidate_must_exist(self) -> TranslationUnit:
        if self.selected_candidate_id is not None:
            known = {candidate.id for candidate in self.candidates}
            if self.selected_candidate_id not in known:
                raise ValueError("selected_candidate_id must reference a candidate in this unit")
        return self


class EditRecord(MfoModel):
    """An append-only record of a human (or auto-applied) change (invariant I-3)."""

    id: str = Field(default_factory=partial(new_id, "edt"))
    translation_unit_id: str
    before: str
    after: str
    action: EditAction
    editor: str = "user"
    timestamp: datetime = Field(default_factory=_utcnow)


class RenderArtifact(MfoModel):
    """A produced page render and the parameters used to create it."""

    id: str = Field(default_factory=partial(new_id, "rnd"))
    page_id: str
    output_path: str
    params: dict[str, Any] = Field(default_factory=dict)


class HistoryEntry(MfoModel):
    """One undoable edit on a page, capturing its before/after state for undo/redo (FR-42, I-2/I-3).

    Every review mutation (a region op or a translation edit) is page-scoped, so an entry stores a
    snapshot of the affected page's regions, OCR spans, and translation units *before* and *after*
    the change. Undo restores ``before``; redo restores ``after``. ``seq`` is a per-project
    monotonic counter giving a strict total order; ``undone`` marks a rolled-back entry. Filtering
    by ``page_id`` yields a per-page history; ignoring it yields the global one.
    """

    id: str = Field(default_factory=partial(new_id, "hist"))
    page_id: str
    seq: int
    action: str  # a short human-readable label, e.g. "delete_region"
    editor: str = "user"
    timestamp: datetime = Field(default_factory=_utcnow)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    undone: bool = False


class Assignment(MfoModel):
    """A reviewer's claim on a page, for basic collaborative assignment (SG-10).

    Lightweight, ephemeral collaboration state: at most one claim per page (keyed by ``page_id``),
    so claiming a page replaces any prior claim. Deliberately *not* part of the page-edit graph or
    the undo/redo history — it coordinates who is working where, it does not change page content.
    """

    id: str = Field(default_factory=partial(new_id, "asn"))
    page_id: str
    editor: str
    timestamp: datetime = Field(default_factory=_utcnow)
