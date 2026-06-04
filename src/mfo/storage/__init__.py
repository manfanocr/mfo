"""Storage layer: project files, SQLite, caches, and export formats."""

from __future__ import annotations

from mfo.storage.assist import DEFAULT_MIN_CONFIDENCE, Suggested, assist_units
from mfo.storage.atomic import atomic_write_bytes, atomic_write_text
from mfo.storage.cache import Cache
from mfo.storage.confidence import (
    ConfidenceReport,
    confidence_report,
    flag_low_confidence,
    low_confidence_regions,
    region_confidences,
)
from mfo.storage.db import SCHEMA_VERSION, Database
from mfo.storage.detect import RegionCandidate, detect_regions
from mfo.storage.edits import list_edits, record_edit
from mfo.storage.export import EXPORT_VERSION, ExportedPage, ExportResult, export_pages
from mfo.storage.grouping import group_into_units
from mfo.storage.hashing import content_key, sha256_bytes, sha256_file
from mfo.storage.ingest import SourceImage, import_pages
from mfo.storage.layout import DB_NAME, MANIFEST_NAME, SUBDIRS, ProjectLayout
from mfo.storage.manifest import MANIFEST_VERSION, Manifest, read_manifest, write_manifest
from mfo.storage.mapping import MAPPING_VERSION, build_mapping, write_mapping
from mfo.storage.ocr import RecognizedSpan, ocr_regions
from mfo.storage.pipeline_state import JsonStateStore
from mfo.storage.preprocess import preprocess_pages
from mfo.storage.presets import (
    SERIES_PRESETS_VERSION,
    load_series_presets,
    save_series_presets,
)
from mfo.storage.project import ProjectStore
from mfo.storage.reading_order import assign_reading_order
from mfo.storage.render import (
    MASK_KIND,
    RENDER_KIND,
    PagePlacement,
    composite_pages,
    mask_pages,
    page_placements,
)
from mfo.storage.series import (
    SERIES_GLOSSARY_VERSION,
    load_series_glossary,
    save_series_glossary,
)
from mfo.storage.translate import Translated, translate_units

__all__ = [
    # atomic
    "atomic_write_bytes",
    "atomic_write_text",
    # hashing
    "content_key",
    "sha256_bytes",
    "sha256_file",
    # cache
    "Cache",
    # confidence
    "ConfidenceReport",
    "confidence_report",
    "flag_low_confidence",
    "low_confidence_regions",
    "region_confidences",
    # layout
    "ProjectLayout",
    "MANIFEST_NAME",
    "DB_NAME",
    "SUBDIRS",
    # manifest
    "Manifest",
    "MANIFEST_VERSION",
    "read_manifest",
    "write_manifest",
    # db
    "Database",
    "SCHEMA_VERSION",
    # pipeline state
    "JsonStateStore",
    # ingest
    "import_pages",
    "SourceImage",
    # preprocess
    "preprocess_pages",
    # detect
    "detect_regions",
    "RegionCandidate",
    # ocr
    "ocr_regions",
    "RecognizedSpan",
    # reading order
    "assign_reading_order",
    # series glossary
    "SERIES_GLOSSARY_VERSION",
    "load_series_glossary",
    "save_series_glossary",
    # series presets
    "SERIES_PRESETS_VERSION",
    "load_series_presets",
    "save_series_presets",
    # grouping
    "group_into_units",
    # translate
    "translate_units",
    "Translated",
    # assist (AI layer, batch 7.2)
    "assist_units",
    "Suggested",
    "DEFAULT_MIN_CONFIDENCE",
    # edits
    "record_edit",
    "list_edits",
    # mapping
    "build_mapping",
    "write_mapping",
    "MAPPING_VERSION",
    # render
    "mask_pages",
    "MASK_KIND",
    "composite_pages",
    "page_placements",
    "PagePlacement",
    "RENDER_KIND",
    # export
    "export_pages",
    "ExportResult",
    "ExportedPage",
    "EXPORT_VERSION",
    # project
    "ProjectStore",
]
