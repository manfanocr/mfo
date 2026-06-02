"""Enumerations used across the mfo data model.

All enums derive from ``str`` so they serialize to plain JSON strings and remain stable,
human-readable values in manifests and exports (NFR-30).
"""

from __future__ import annotations

from enum import StrEnum


class RegionType(StrEnum):
    """Kind of text region detected on a page (spec FR-11)."""

    BUBBLE = "bubble"
    NARRATION = "narration"
    SFX = "sfx"
    CAPTION = "caption"
    SIDE_TEXT = "side_text"
    UNKNOWN = "unknown"


class RegionStatus(StrEnum):
    """Review status of a region (spec FR-40)."""

    AUTO = "auto"  # produced by automation, not yet reviewed
    CORRECT = "correct"
    NEEDS_REVIEW = "needs_review"
    IGNORE = "ignore"
    MANUAL = "manual"  # requires manual transcription


class ReadingDirection(StrEnum):
    """Page reading direction (spec FR-17)."""

    RTL = "rtl"  # right-to-left, top-to-bottom (manga/manhua default)
    LTR = "ltr"  # left-to-right, top-to-bottom (western)


class TranslationStyle(StrEnum):
    """Requested translation register (spec FR-25)."""

    LITERAL = "literal"
    BALANCED = "balanced"
    NATURAL = "natural"
    LOCALIZED = "localized"


class CandidateKind(StrEnum):
    """Provenance of a translation candidate (spec §12.3)."""

    RAW = "raw"  # direct machine translation
    LITERAL = "literal"
    NATURAL = "natural"
    AI = "ai"  # AI-assisted rewrite
    MANUAL = "manual"  # human-entered


class EditAction(StrEnum):
    """Type of change recorded in an EditRecord (spec FR-42)."""

    EDIT_TRANSLATION = "edit_translation"
    SELECT_CANDIDATE = "select_candidate"
    EDIT_OCR = "edit_ocr"
    SET_STATUS = "set_status"
    SPLIT_REGION = "split_region"
    MERGE_REGION = "merge_region"
    REORDER = "reorder"
