"""Vision layer: image ingest, region detection, and OCR adapters."""

from __future__ import annotations

from mfo.vision.images import (
    SUPPORTED_SUFFIXES,
    ImageError,
    is_supported,
    read_image_size,
)
from mfo.vision.ingest import (
    DiscoveredImage,
    ImportScan,
    PageOrder,
    SkippedImage,
    discover_images,
)
from mfo.vision.preprocess import (
    PreprocessConfig,
    detect_orientation,
    estimate_skew_angle,
    preprocess_file,
    preprocess_image,
)

__all__ = [
    # images
    "SUPPORTED_SUFFIXES",
    "ImageError",
    "is_supported",
    "read_image_size",
    # ingest
    "DiscoveredImage",
    "ImportScan",
    "PageOrder",
    "SkippedImage",
    "discover_images",
    # preprocess
    "PreprocessConfig",
    "detect_orientation",
    "estimate_skew_angle",
    "preprocess_file",
    "preprocess_image",
]
