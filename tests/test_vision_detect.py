"""Tests for the region detection adapter + connected-components baseline (§10.3; FR-10/11)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mfo.core.enums import RegionType
from mfo.vision.detect import (
    ConnectedComponentsDetector,
    DetectedRegion,
    detect_file,
    get_detector,
)


def _page_with_blocks() -> np.ndarray:
    """A white page with three solid black blocks of distinct shapes (bubble/wide/tall)."""
    img = np.full((300, 200, 3), 255, dtype=np.uint8)
    img[20:40, 20:60] = 0  # 40x20 compact block  → bubble
    img[80:90, 20:100] = 0  # 80x10 wide block     → narration
    img[120:160, 20:30] = 0  # 10x40 tall strip    → side text
    return img


def _bbox_near(
    region: DetectedRegion, x: float, y: float, w: float, h: float, tol: int = 4
) -> bool:
    b = region.bbox
    return (
        abs(b.x - x) <= tol
        and abs(b.y - y) <= tol
        and abs(b.width - w) <= tol
        and abs(b.height - h) <= tol
    )


def test_baseline_detects_blocks_in_reading_order() -> None:
    regions = ConnectedComponentsDetector().detect(_page_with_blocks())
    assert len(regions) == 3
    # Sorted top-to-bottom.
    assert [r.type for r in regions] == [
        RegionType.BUBBLE,
        RegionType.NARRATION,
        RegionType.SIDE_TEXT,
    ]
    assert _bbox_near(regions[0], 20, 20, 40, 20)
    assert _bbox_near(regions[1], 20, 80, 80, 10)
    assert _bbox_near(regions[2], 20, 120, 10, 40)


def test_confidence_is_bounded() -> None:
    for region in ConnectedComponentsDetector().detect(_page_with_blocks()):
        assert 0.0 <= region.confidence <= 1.0


def test_blank_page_yields_no_regions() -> None:
    blank = np.full((120, 120, 3), 255, dtype=np.uint8)
    assert ConnectedComponentsDetector().detect(blank) == []


def test_tiny_speck_is_filtered_out() -> None:
    img = np.full((300, 300, 3), 255, dtype=np.uint8)
    img[10:12, 10:12] = 0  # 2x2 speck — below min area fraction
    assert ConnectedComponentsDetector().detect(img) == []


def test_get_detector_returns_baseline_and_rejects_unknown() -> None:
    assert isinstance(get_detector("baseline"), ConnectedComponentsDetector)
    with pytest.raises(ValueError, match="unknown detector"):
        get_detector("does-not-exist")


def test_detect_file_reads_image_and_detects(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.fromarray(_page_with_blocks(), mode="RGB").save(path)
    regions = detect_file(path, ConnectedComponentsDetector())
    assert len(regions) == 3
