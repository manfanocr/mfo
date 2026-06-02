"""Core layer: data models, project state, and pipeline orchestration."""

from __future__ import annotations

from mfo.core.confidence import (
    DEFAULT_THRESHOLD,
    aggregate_confidence,
    is_low_confidence,
)
from mfo.core.enums import (
    CandidateKind,
    EditAction,
    ReadingDirection,
    RegionStatus,
    RegionType,
    TranslationStyle,
)
from mfo.core.geometry import BBox, Point
from mfo.core.ids import new_id, new_ulid
from mfo.core.models import (
    EditRecord,
    MfoModel,
    OCRSpan,
    Page,
    Project,
    Region,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.pipeline import (
    InMemoryStateStore,
    Pipeline,
    Stage,
    StageRecord,
    StageResult,
    StageStatus,
    StateStore,
)
from mfo.core.reading_order import order_regions

__all__ = [
    # confidence
    "DEFAULT_THRESHOLD",
    "aggregate_confidence",
    "is_low_confidence",
    # reading order
    "order_regions",
    # ids
    "new_id",
    "new_ulid",
    # geometry
    "BBox",
    "Point",
    # enums
    "CandidateKind",
    "EditAction",
    "ReadingDirection",
    "RegionStatus",
    "RegionType",
    "TranslationStyle",
    # models
    "MfoModel",
    "Project",
    "Page",
    "Region",
    "OCRSpan",
    "TranslationCandidate",
    "TranslationUnit",
    "EditRecord",
    "RenderArtifact",
    # pipeline
    "Pipeline",
    "Stage",
    "StageRecord",
    "StageResult",
    "StageStatus",
    "StateStore",
    "InMemoryStateStore",
]
