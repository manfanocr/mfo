"""Core layer: data models, project state, and pipeline orchestration."""

from __future__ import annotations

from mfo.core.confidence import (
    DEFAULT_THRESHOLD,
    aggregate_confidence,
    is_low_confidence,
)
from mfo.core.context import DEFAULT_NEIGHBOR_WINDOW, build_context
from mfo.core.enums import (
    AssistMode,
    CandidateKind,
    EditAction,
    ReadingDirection,
    RegionStatus,
    RegionType,
    SfxMode,
    TranslationStyle,
)
from mfo.core.geometry import BBox, Point
from mfo.core.glossary import (
    GlossaryEntry,
    applicable_entries,
    apply_glossary,
    entries_from_config,
    entries_to_config,
    glossary_terms,
    merge_glossaries,
)
from mfo.core.grouping import group_regions
from mfo.core.ids import new_id, new_ulid
from mfo.core.models import (
    EditRecord,
    HistoryEntry,
    MfoModel,
    OCRSpan,
    Page,
    Project,
    Region,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.parallel import parallel_map, resolve_jobs
from mfo.core.pipeline import (
    InMemoryStateStore,
    Pipeline,
    Stage,
    StageRecord,
    StageResult,
    StageStatus,
    StateStore,
)
from mfo.core.plugins import (
    ASSISTANT_GROUP,
    DETECTOR_GROUP,
    OCR_GROUP,
    RENDERER_GROUP,
    TRANSLATOR_GROUP,
    discover_plugins,
    resolve_factory,
)
from mfo.core.presets import (
    RenderPreset,
    SeriesPreset,
    SeriesPresetStore,
    find_preset,
    remove_preset,
    series_preset_names,
    upsert_preset,
)
from mfo.core.reading_order import order_regions, order_regions_by_panels, panel_of
from mfo.core.series import (
    SeriesGlossary,
    merge_entries,
    remove_entry,
    upsert_entry,
)
from mfo.core.traceability import selected_candidate, selected_text

__all__ = [
    # confidence
    "DEFAULT_THRESHOLD",
    "aggregate_confidence",
    "is_low_confidence",
    # context
    "DEFAULT_NEIGHBOR_WINDOW",
    "build_context",
    # reading order
    "order_regions",
    "order_regions_by_panels",
    "panel_of",
    # traceability
    "selected_candidate",
    "selected_text",
    # glossary
    "GlossaryEntry",
    "apply_glossary",
    "applicable_entries",
    "entries_from_config",
    "entries_to_config",
    "glossary_terms",
    "merge_glossaries",
    # series glossary
    "SeriesGlossary",
    "merge_entries",
    "remove_entry",
    "upsert_entry",
    # series presets
    "RenderPreset",
    "SeriesPreset",
    "SeriesPresetStore",
    "find_preset",
    "remove_preset",
    "series_preset_names",
    "upsert_preset",
    # grouping
    "group_regions",
    # ids
    "new_id",
    "new_ulid",
    # geometry
    "BBox",
    "Point",
    # enums
    "AssistMode",
    "CandidateKind",
    "EditAction",
    "ReadingDirection",
    "RegionStatus",
    "RegionType",
    "SfxMode",
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
    "HistoryEntry",
    "RenderArtifact",
    # pipeline
    "Pipeline",
    "Stage",
    "StageRecord",
    "StageResult",
    "StageStatus",
    "StateStore",
    "InMemoryStateStore",
    # parallel
    "parallel_map",
    "resolve_jobs",
    # plugins
    "ASSISTANT_GROUP",
    "DETECTOR_GROUP",
    "OCR_GROUP",
    "RENDERER_GROUP",
    "TRANSLATOR_GROUP",
    "discover_plugins",
    "resolve_factory",
]
