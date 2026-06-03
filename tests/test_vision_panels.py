"""Tests for best-effort panel detection (batch 3.3; FR-18; SG-1 groundwork).

The detector is a recursive X–Y cut over white gutters, so synthetic pages with black panel frames
separated by gutters exercise it deterministically.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from mfo.vision.panels import Panel, PanelConfig, detect_panels, detect_panels_file


def _blank(height: int = 200, width: int = 200) -> np.ndarray:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def _frame(canvas: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> None:
    """Draw a black panel border (outlined rectangle) on ``canvas``."""
    cv2.rectangle(canvas, (x0, y0), (x1, y1), (0, 0, 0), thickness=3)


def _corners(panels: list[Panel], tol: int = 6) -> list[tuple[int, int]]:
    return [(round(p.bbox.x), round(p.bbox.y)) for p in panels]


def _near(actual: tuple[int, int], expected: tuple[int, int], tol: int = 6) -> bool:
    return abs(actual[0] - expected[0]) <= tol and abs(actual[1] - expected[1]) <= tol


def test_blank_page_yields_no_panels() -> None:
    assert detect_panels(_blank()) == []


def test_single_frame_is_one_panel() -> None:
    page = _blank()
    _frame(page, 10, 10, 190, 190)
    panels = detect_panels(page)
    assert len(panels) == 1
    assert _near(_corners(panels)[0], (10, 10))
    assert panels[0].bbox.width > 150 and panels[0].bbox.height > 150


def test_two_by_two_grid_splits_into_four_panels() -> None:
    page = _blank()
    _frame(page, 10, 10, 90, 90)  # top-left
    _frame(page, 110, 10, 190, 90)  # top-right
    _frame(page, 10, 110, 90, 190)  # bottom-left
    _frame(page, 110, 110, 190, 190)  # bottom-right

    panels = detect_panels(page)
    assert len(panels) == 4
    corners = _corners(panels)
    for expected in [(10, 10), (110, 10), (10, 110), (110, 110)]:
        assert any(_near(c, expected) for c in corners), f"missing panel near {expected}"


def test_tall_panel_beside_a_stack_is_segmented() -> None:
    # The tricky layout: one full-height panel on the left, two stacked on the right.
    page = _blank()
    _frame(page, 10, 10, 90, 190)  # left, full height
    _frame(page, 110, 10, 190, 90)  # right-top
    _frame(page, 110, 110, 190, 190)  # right-bottom

    panels = detect_panels(page)
    assert len(panels) == 3
    corners = _corners(panels)
    for expected in [(10, 10), (110, 10), (110, 110)]:
        assert any(_near(c, expected) for c in corners), f"missing panel near {expected}"
    # The left panel spans most of the page height; the right ones do not.
    left = next(p for p in panels if _near((round(p.bbox.x), round(p.bbox.y)), (10, 10)))
    assert left.bbox.height > 150


def test_tiny_specks_are_filtered_by_min_panel_frac() -> None:
    page = _blank()
    _frame(page, 10, 10, 190, 190)  # a real panel
    page[2:5, 2:5] = 0  # a 3x3 ink speck in the margin, far below min_panel_frac
    panels = detect_panels(page)
    assert len(panels) == 1  # the speck is discarded


def test_config_overrides_are_respected() -> None:
    page = _blank()
    _frame(page, 10, 10, 90, 90)
    _frame(page, 110, 10, 190, 90)
    # A gutter requirement wider than the actual ~17px gutter prevents the vertical split.
    coarse = detect_panels(page, PanelConfig(min_gutter_frac=0.3))
    assert len(coarse) == 1


def test_detect_panels_file_reads_image_read_only(tmp_path: Path) -> None:
    page = _blank()
    _frame(page, 10, 10, 90, 190)
    _frame(page, 110, 10, 190, 90)
    _frame(page, 110, 110, 190, 190)
    path = tmp_path / "page.png"
    Image.fromarray(page, mode="RGB").save(path)
    before = path.read_bytes()

    boxes = detect_panels_file(path)
    assert len(boxes) == 3
    assert path.read_bytes() == before  # original untouched (I-1)
