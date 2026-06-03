"""Render layer: masking, font fitting, text placement, and compositing."""

from __future__ import annotations

from mfo.render.mask import (
    MaskArtifact,
    MaskConfig,
    estimate_background,
    mask_file,
    mask_image,
    restore,
)

__all__ = [
    # masking
    "MaskArtifact",
    "MaskConfig",
    "mask_file",
    "mask_image",
    "estimate_background",
    "restore",
]
