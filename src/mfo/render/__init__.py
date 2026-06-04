"""Render layer: masking, font fitting, text placement, and compositing."""

from __future__ import annotations

from mfo.render.composite import (
    CompositeArtifact,
    CompositeResult,
    PlacedText,
    Placement,
    composite_file,
    composite_page,
)
from mfo.render.mask import (
    MaskArtifact,
    MaskConfig,
    estimate_background,
    mask_file,
    mask_image,
    restore,
)
from mfo.render.shape import band_inner, scanline_span
from mfo.render.typeset import (
    DEFAULT_PRESET,
    PRESETS,
    FontLoader,
    StylePreset,
    TextLayout,
    fit_text,
    get_preset,
    load_font,
    preset_names,
    render_layout,
    typeset,
    wrap_text,
)

__all__ = [
    # masking
    "MaskArtifact",
    "MaskConfig",
    "mask_file",
    "mask_image",
    "estimate_background",
    "restore",
    # typesetting
    "StylePreset",
    "TextLayout",
    "FontLoader",
    "PRESETS",
    "DEFAULT_PRESET",
    "preset_names",
    "get_preset",
    "load_font",
    "wrap_text",
    "fit_text",
    "render_layout",
    "typeset",
    # shape (bubble-aware fitting, SG-6)
    "band_inner",
    "scanline_span",
    # compositing
    "Placement",
    "PlacedText",
    "CompositeResult",
    "CompositeArtifact",
    "composite_page",
    "composite_file",
]
