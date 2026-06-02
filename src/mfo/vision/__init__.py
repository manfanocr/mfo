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
]
