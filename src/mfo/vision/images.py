"""Image-reading adapter for the vision layer.

A thin seam over Pillow so the rest of the pipeline reads image dimensions without depending
on a specific imaging library (NFR-17). Malformed or unreadable images raise :class:`ImageError`
with an actionable message so callers can skip them gracefully (NFR-9).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError

# Raster formats we accept as input pages (FR-1). Suffixes are matched case-insensitively.
SUPPORTED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})


class ImageError(Exception):
    """Raised when an image cannot be read or decoded."""


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def read_image_size(path: Path) -> tuple[int, int]:
    """Return ``(width, height)`` for the image at ``path`` without mutating it (I-1)."""
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError(f"cannot read image {Path(path).name}: {exc}") from exc
