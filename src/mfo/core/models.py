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
    """Base model: forbid unknown fields for strict, stable schemas."""

    model_config = ConfigDict(extra="forbid")


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


class Region(MfoModel):
    """A detected text region on a page."""

    id: str = Field(default_factory=partial(new_id, "rgn"))
    page_id: str
    bbox: BBox
    polygon: list[Point] | None = None
    type: RegionType = RegionType.UNKNOWN
    reading_order_index: int | None = None
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
