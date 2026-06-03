"""Best-effort panel boundary detection (spec §10.3, §10.5; FR-18; SG-1 groundwork).

Panels are a *layout* signal, not a content one: knowing where a page's frames are lets reading
order be derived panel-by-panel instead of by a single page-wide tier scan, which fixes the hard
cases a flat scan gets wrong (a tall panel spanning several tiers beside a stack of small ones).

The detector is deliberately **light and optional**: it uses a recursive X–Y cut over the page's
white gutters — the margins between frames carry no ink, so projecting ink onto each axis and
cutting at the widest empty bands recovers the frame rectangles for the common grid layouts with no
model download (NFR-21), using only OpenCV/NumPy (already core dependencies). It is best-effort by
design (FR-18, "when useful"): a borderless or art-bleed page simply yields one whole-page panel,
and the caller falls back to the flat reading-order heuristic — panel-awareness is never required
for the core path. Panels come back as :class:`~mfo.core.geometry.BBox` boxes in source-image pixel
space (origin top-left), matching the rest of the vision layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from mfo.core.geometry import BBox

Uint8Array = NDArray[np.uint8]
BoolArray = NDArray[np.bool_]


@dataclass(frozen=True)
class PanelConfig:
    """Thresholds for the X–Y-cut panel detector.

    ``ink_threshold`` separates content/borders (darker) from page/gutter (lighter). A gutter is a
    band of empty lines at least ``min_gutter_frac`` of the page's short edge wide; frames smaller
    than ``min_panel_frac`` of the page area are discarded as noise. ``max_depth`` bounds the
    recursion so a pathological page can't split forever.
    """

    ink_threshold: int = 200
    min_gutter_frac: float = 0.02
    min_panel_frac: float = 0.01
    max_depth: int = 8


@dataclass(frozen=True)
class Panel:
    """A detected panel (frame) in source-image pixel space."""

    bbox: BBox


def _to_gray(image: Uint8Array) -> Uint8Array:
    if image.ndim == 2:
        return image
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)


def _content_segments(has_ink: BoolArray, min_gap: int) -> list[tuple[int, int]]:
    """Split a 1-D ink profile into content runs separated by empty gaps of ``min_gap`` or more.

    Returns ``(start, end)`` half-open spans of the inked runs; leading/trailing empty lines (page
    margins) are dropped, and gaps shorter than ``min_gap`` are kept inside a single run.
    """
    inked = np.flatnonzero(has_ink)
    if inked.size == 0:
        return []
    segments: list[tuple[int, int]] = []
    start = prev = int(inked[0])
    for index in inked[1:]:
        current = int(index)
        if current - prev - 1 >= min_gap:
            segments.append((start, prev + 1))
            start = current
        prev = current
    segments.append((start, prev + 1))
    return segments


def _segment(
    ink: BoolArray, x0: int, y0: int, width: int, height: int, config: PanelConfig, depth: int
) -> list[BBox]:
    """Recursively cut a sub-rectangle of the ink mask along its widest interior gutter."""
    sub = ink[y0 : y0 + height, x0 : x0 + width]
    short_edge = min(ink.shape[0], ink.shape[1])
    min_gap = max(1, round(short_edge * config.min_gutter_frac))

    rows_have_ink: BoolArray = np.asarray(sub.any(axis=1), dtype=np.bool_)
    cols_have_ink: BoolArray = np.asarray(sub.any(axis=0), dtype=np.bool_)

    if depth < config.max_depth:
        # Prefer horizontal cuts (rows): manga tiers stack top-to-bottom.
        row_segments = _content_segments(rows_have_ink, min_gap)
        if len(row_segments) > 1:
            boxes: list[BBox] = []
            for top, bottom in row_segments:
                boxes += _segment(ink, x0, y0 + top, width, bottom - top, config, depth + 1)
            return boxes

        col_segments = _content_segments(cols_have_ink, min_gap)
        if len(col_segments) > 1:
            boxes = []
            for left, right in col_segments:
                boxes += _segment(ink, x0 + left, y0, right - left, height, config, depth + 1)
            return boxes

    # Leaf: trim to the tight bounding box of whatever ink remains (drop if empty).
    rows = np.flatnonzero(rows_have_ink)
    cols = np.flatnonzero(cols_have_ink)
    if rows.size == 0 or cols.size == 0:
        return []
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    return [
        BBox(
            x=float(x0 + left),
            y=float(y0 + top),
            width=float(right - left),
            height=float(bottom - top),
        )
    ]


def detect_panels(image: Uint8Array, config: PanelConfig | None = None) -> list[Panel]:
    """Detect panel frames on a page via recursive X–Y cut over its white gutters (best-effort).

    Returns panels sorted top-to-bottom, left-to-right (a stable raster order; reading order proper
    is layered on top in :func:`mfo.core.reading_order.order_regions_by_panels`). A page with no
    usable gutters yields a single page-sized panel, signalling the caller to fall back to the flat
    reading-order heuristic.
    """
    config = config or PanelConfig()
    height, width = image.shape[:2]
    if height == 0 or width == 0:
        return []

    gray = _to_gray(image)
    ink: BoolArray = gray < config.ink_threshold

    page_area = float(height * width)
    min_area = page_area * config.min_panel_frac
    boxes = [box for box in _segment(ink, 0, 0, width, height, config, 0) if box.area >= min_area]
    boxes.sort(key=lambda b: (b.y, b.x))
    return [Panel(bbox=box) for box in boxes]


def detect_panels_file(path: Path, config: PanelConfig | None = None) -> list[BBox]:
    """Load the image at ``path`` (read-only, I-1) and return its detected panel boxes."""
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return [panel.bbox for panel in detect_panels(array, config)]
