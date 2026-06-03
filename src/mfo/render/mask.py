"""Text masking / removal for the render stage (spec §10.8; FR-31/32/33; I-1/I-6, NFR-26).

Pure and storage-free. Given a page image and the text regions detected on it, produce:

* a **masked** page with the source text removed by filling each region with its estimated
  local background colour — a best-effort background reconstruction (FR-32) that keeps coloured
  or screentoned bubbles looking right rather than punching white holes; and
* a 1-channel **mask** layer (white where pixels were altered, black elsewhere) that records
  exactly what changed, so the operation is fully reversible (I-6, :func:`restore`).

Masking is strictly confined to region boxes, so line art and texture *outside* text regions are
preserved byte-for-byte (FR-33). The source image is opened read-only and never mutated (I-1).
Every step is deterministic, so the same page + regions always yield identical bytes (NFR-26).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mfo.core.geometry import BBox

# Fill colour used when a region's surroundings give us nothing to sample (e.g. it spans the
# whole image). White is the safe neutral default for the common white-bubble case.
_DEFAULT_BACKGROUND = (255, 255, 255)


@dataclass(frozen=True)
class MaskConfig:
    """Knobs for masking. ``pad`` grows each box to catch anti-aliased text edges; ``border`` is
    the width of the ring sampled *outside* a box to estimate its background colour."""

    pad: int = 2
    border: int = 4

    def signature(self) -> str:
        """A stable string identifying this config, for content-addressed caching (NFR-7/8)."""
        return f"mask@1;pad={self.pad};border={self.border}"


# Module-level default so callers can omit a config without paying a B008 default-arg call.
_DEFAULT_CONFIG = MaskConfig()


@dataclass(frozen=True)
class MaskArtifact:
    """The PNG bytes of a masked page and its mask layer, plus describing metadata."""

    masked_png: bytes
    mask_png: bytes
    metadata: dict[str, Any]


def _clamp_box(box: BBox, width: int, height: int, pad: int) -> tuple[int, int, int, int] | None:
    """Convert a float bbox to an integer ``(left, top, right, bottom)`` clamped to the image.

    Returns ``None`` for a box that lands fully outside the image or collapses to nothing.
    """
    left = max(0, int(math.floor(box.x)) - pad)
    top = max(0, int(math.floor(box.y)) - pad)
    right = min(width, int(math.ceil(box.right)) + pad)
    bottom = min(height, int(math.ceil(box.bottom)) + pad)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def estimate_background(
    arr: np.ndarray, box: tuple[int, int, int, int], border: int
) -> tuple[int, int, int]:
    """Estimate the background colour around ``box`` as the median of a ring just outside it.

    Sampling the bubble interior *around* the text (rather than the text itself) recovers the
    fill colour for solid and lightly textured backgrounds alike. The median is robust to the
    odd stray ink pixel and is fully deterministic.
    """
    left, top, right, bottom = box
    height, width = arr.shape[:2]
    outer_left = max(0, left - border)
    outer_top = max(0, top - border)
    outer_right = min(width, right + border)
    outer_bottom = min(height, bottom + border)

    outer = arr[outer_top:outer_bottom, outer_left:outer_right]
    if outer.size == 0:
        return _DEFAULT_BACKGROUND

    # Mask out the inner (text) box within the outer crop, leaving only the surrounding ring.
    ring_mask = np.ones(outer.shape[:2], dtype=bool)
    inner_left = left - outer_left
    inner_top = top - outer_top
    ring_mask[inner_top : inner_top + (bottom - top), inner_left : inner_left + (right - left)] = (
        False
    )

    ring = outer[ring_mask]
    if ring.size == 0:
        return _DEFAULT_BACKGROUND
    median = np.median(ring.reshape(-1, arr.shape[2]), axis=0)
    return (int(round(median[0])), int(round(median[1])), int(round(median[2])))


def mask_image(
    image: Image.Image, boxes: list[BBox], config: MaskConfig
) -> tuple[Image.Image, Image.Image]:
    """Return ``(masked, mask)`` images for ``image`` with the text in ``boxes`` removed.

    ``masked`` is an RGB copy with each box filled by its estimated background colour; ``mask`` is
    an ``L`` image, white where a pixel was altered. The input image is not modified (I-1).
    """
    rgb = image.convert("RGB")
    arr = np.asarray(rgb).copy()
    height, width = arr.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)

    for box in boxes:
        clamped = _clamp_box(box, width, height, config.pad)
        if clamped is None:
            continue
        left, top, right, bottom = clamped
        background = estimate_background(arr, clamped, config.border)
        arr[top:bottom, left:right] = background
        mask[top:bottom, left:right] = 255

    return Image.fromarray(arr, mode="RGB"), Image.fromarray(mask, mode="L")


def restore(masked: Image.Image, mask: Image.Image, original: Image.Image) -> Image.Image:
    """Reverse a masking: paste the original pixels back wherever ``mask`` is set (I-6).

    Outside the mask, ``masked`` already equals the original, so the result reproduces the source
    image exactly — demonstrating that masking destroys nothing the mask layer can't undo.
    """
    base = np.asarray(masked.convert("RGB")).copy()
    source = np.asarray(original.convert("RGB"))
    altered = np.asarray(mask.convert("L")) > 0
    base[altered] = source[altered]
    return Image.fromarray(base, mode="RGB")


def _to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def mask_file(path: Path, boxes: list[BBox], config: MaskConfig = _DEFAULT_CONFIG) -> MaskArtifact:
    """Read the image at ``path`` (read-only) and return its masked + mask PNG bytes (I-1)."""
    with Image.open(path) as image:
        image.load()
        masked, mask = mask_image(image, boxes, config)
    return MaskArtifact(
        masked_png=_to_png(masked),
        mask_png=_to_png(mask),
        metadata={
            "regions": len(boxes),
            "config": config.signature(),
            "size": [masked.width, masked.height],
        },
    )
