"""Page preprocessing for analysis (spec §10.2; FR-3 non-destructive; NFR-5).

Produces a normalized *analysis* derivative of a page — consistent color space, optional
downscale, optional denoise/deskew — while the original is never touched (the derivative is
returned as bytes for the storage layer to cache, I-1/FR-3). The recorded ``scale`` lets later
stages map coordinates on the derivative back to the original image (I-2).

This module is pure and storage-free so it stays easy to test; the storage layer injects the
``preprocess_file`` callable and handles caching/persistence.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True)
class PreprocessConfig:
    """Knobs for the preprocessing stage. Deskew and denoise are off by default (§10.2)."""

    grayscale: bool = False
    max_dimension: int | None = None
    denoise: bool = False
    deskew: bool = False

    def signature(self) -> str:
        """A stable string identifying this config, for content-addressed caching (NFR-7/8)."""
        return (
            f"grayscale={self.grayscale};max_dimension={self.max_dimension};"
            f"denoise={self.denoise};deskew={self.deskew}"
        )


def detect_orientation(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def estimate_skew_angle(image: Image.Image, *, limit: float = 7.0, step: float = 1.0) -> float:
    """Estimate the rotation (degrees) that best aligns horizontal structure.

    Scores each candidate angle by the variance of the vertical projection profile's gradient —
    a classic, dependency-light deskew heuristic. The returned angle is the correction to apply
    (rotate the image by it to straighten the page).
    """
    gray = np.asarray(image.convert("L"), dtype=np.float64)
    base = Image.fromarray(gray.astype(np.uint8))
    best_angle = 0.0
    best_score = -1.0
    steps = int(round((2 * limit) / step))
    for i in range(steps + 1):
        angle = -limit + i * step
        rotated = base.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
        profile = np.asarray(rotated, dtype=np.float64).sum(axis=1)
        score = float(np.var(np.diff(profile)))
        if score > best_score:
            best_score = score
            best_angle = angle
    return best_angle


def preprocess_image(
    image: Image.Image, config: PreprocessConfig
) -> tuple[Image.Image, dict[str, Any]]:
    """Apply the configured operations, returning the derivative and its metadata."""
    operations: list[str] = []
    source_size = (image.width, image.height)

    target_mode = "L" if config.grayscale else "RGB"
    result = image if image.mode == target_mode else image.convert(target_mode)
    operations.append(f"color:{target_mode}")

    deskew_angle: float | None = None
    if config.deskew:
        deskew_angle = estimate_skew_angle(result)
        if deskew_angle != 0.0:
            fill: int | tuple[int, int, int] = 255 if target_mode == "L" else (255, 255, 255)
            result = result.rotate(deskew_angle, resample=Image.Resampling.BILINEAR, fillcolor=fill)
        operations.append("deskew")

    if config.denoise:
        result = result.filter(ImageFilter.MedianFilter(size=3))
        operations.append("denoise")

    scale = 1.0
    if config.max_dimension is not None:
        longest = max(result.width, result.height)
        if longest > config.max_dimension:
            scale = config.max_dimension / longest
            new_size = (max(1, round(result.width * scale)), max(1, round(result.height * scale)))
            result = result.resize(new_size, resample=Image.Resampling.LANCZOS)
            operations.append(f"downscale:{config.max_dimension}")

    metadata: dict[str, Any] = {
        "operations": operations,
        "source_size": list(source_size),
        "output_size": [result.width, result.height],
        "scale": scale,
        "orientation": detect_orientation(*source_size),
        "color_mode": target_mode,
        "deskew_angle": deskew_angle,
    }
    return result, metadata


def preprocess_file(path: Path, config: PreprocessConfig) -> tuple[bytes, dict[str, Any]]:
    """Read the image at ``path`` (read-only) and return its derivative PNG bytes + metadata."""
    with Image.open(path) as image:
        image.load()
        processed, metadata = preprocess_image(image, config)
    buffer = io.BytesIO()
    processed.save(buffer, format="PNG")
    return buffer.getvalue(), metadata
