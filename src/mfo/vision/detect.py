"""Region detection adapters (spec §10.3; FR-10, FR-11; NFR-17, NFR-21; MVP-3).

Detection is pluggable behind the :class:`RegionDetector` protocol so heavier ML detectors can
be added later (batch 2.2) without touching the pipeline. The default
:class:`ConnectedComponentsDetector` is a dependency-light OpenCV baseline that runs CPU-only and
needs **no model download** (NFR-21), so the project works out of the box.

Detectors operate on a page as a NumPy array and return :class:`DetectedRegion` boxes in
source-image pixel space (origin top-left), matching :mod:`mfo.core.geometry`. The storage layer
turns these into persisted ``Region`` records linked to their page.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from mfo.core.enums import RegionType
from mfo.core.geometry import BBox

Uint8Array = NDArray[np.uint8]


@dataclass(frozen=True)
class DetectedRegion:
    """A candidate text region in source-image pixel space."""

    bbox: BBox
    type: RegionType
    confidence: float


class RegionDetector(Protocol):
    """A swappable region detector (NFR-17). ``name``/``version`` identify it for caching."""

    name: str
    version: str

    def detect(self, image: Uint8Array) -> list[DetectedRegion]: ...


@dataclass(frozen=True)
class BaselineConfig:
    """Heuristic thresholds for the connected-components baseline."""

    min_area_frac: float = 0.0004  # ignore specks smaller than this fraction of the page
    max_area_frac: float = 0.4  # ignore panel-/page-sized blobs
    close_frac: float = 0.015  # morphological-close kernel as a fraction of the short edge
    min_fill: float = 0.12  # min filled fraction of the bounding box to count as text


def _to_gray(image: Uint8Array) -> Uint8Array:
    if image.ndim == 2:
        return image
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)


def _classify(width: int, height: int) -> RegionType:
    """Coarse type guess from shape alone (best-effort; refined by the ML detector in 2.2)."""
    aspect = width / height
    if aspect >= 2.5:
        return RegionType.NARRATION  # wide rectangle → narration/caption box
    if aspect <= 0.4:
        return RegionType.SIDE_TEXT  # tall/vertical strip
    return RegionType.BUBBLE


def _confidence(fill: float, area_frac: float) -> float:
    """A bounded heuristic score: well-filled, plausibly-sized blobs score higher (I-4)."""
    size_score = 1.0 if 0.003 <= area_frac <= 0.25 else 0.6
    return round(min(1.0, (0.4 + 0.5 * fill) * size_score), 3)


class ConnectedComponentsDetector:
    """OpenCV connected-components baseline: threshold → merge glyphs → box the blobs."""

    name = "baseline-cc"
    version = "1"

    def __init__(self, config: BaselineConfig | None = None) -> None:
        self._config = config or BaselineConfig()

    def detect(self, image: Uint8Array) -> list[DetectedRegion]:
        config = self._config
        gray = _to_gray(image)
        height, width = gray.shape[:2]
        page_area = float(height * width)
        if page_area == 0:
            return []

        # Otsu binarization; invert so ink (text/outlines) becomes foreground.
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        # Close gaps between glyphs so a line/block of text becomes one component.
        kernel_size = max(1, round(min(height, width) * config.close_frac))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        merged = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(merged, connectivity=8)

        regions: list[DetectedRegion] = []
        for i in range(1, count):  # 0 is the background component
            x = int(stats[i, cv2.CC_STAT_LEFT])
            y = int(stats[i, cv2.CC_STAT_TOP])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            area = int(stats[i, cv2.CC_STAT_AREA])
            if w == 0 or h == 0:
                continue
            area_frac = area / page_area
            if area_frac < config.min_area_frac or area_frac > config.max_area_frac:
                continue
            fill = area / float(w * h)
            if fill < config.min_fill:
                continue
            regions.append(
                DetectedRegion(
                    bbox=BBox(x=float(x), y=float(y), width=float(w), height=float(h)),
                    type=_classify(w, h),
                    confidence=_confidence(fill, area_frac),
                )
            )
        regions.sort(key=lambda r: (r.bbox.y, r.bbox.x))
        return regions


def baseline_detector() -> RegionDetector:
    return ConnectedComponentsDetector()


_FACTORIES = {"baseline": baseline_detector}


def get_detector(name: str = "baseline") -> RegionDetector:
    """Resolve a detector by config name (NFR-17). Raises ``ValueError`` if unknown."""
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"unknown detector {name!r}; available: {known}") from None
    return factory()


def detect_file(path: Path, detector: RegionDetector) -> list[DetectedRegion]:
    """Load the image at ``path`` (read-only) and run ``detector`` on it (I-1)."""
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return detector.detect(array)
