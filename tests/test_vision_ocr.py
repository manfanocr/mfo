"""Tests for the OCR adapter interface and manga-ocr wiring (§10.4; FR-12/13/15; MVP-4)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mfo.core.geometry import BBox
from mfo.vision.ocr import (
    MangaOcrEngine,
    OcrDependencyError,
    RecognizedText,
    get_ocr_engine,
    recognize_file,
)

_MANGA_OCR_INSTALLED = importlib.util.find_spec("manga_ocr") is not None


class _SpyEngine:
    """A fake engine that records the crop it was handed and echoes its shape as text."""

    name = "spy"
    version = "1"

    def __init__(self) -> None:
        self.last_shape: tuple[int, ...] | None = None

    def recognize(self, image: np.ndarray) -> RecognizedText:
        self.last_shape = image.shape
        return RecognizedText(text=f"{image.shape[1]}x{image.shape[0]}", confidence=0.5)


def test_recognized_text_defaults() -> None:
    span = RecognizedText(text="あ")
    assert span.text == "あ"
    assert span.confidence is None
    assert span.alternatives == []


def test_get_ocr_engine_returns_manga_ocr_and_rejects_unknown() -> None:
    assert isinstance(get_ocr_engine("manga-ocr"), MangaOcrEngine)
    with pytest.raises(ValueError, match="unknown OCR engine"):
        get_ocr_engine("does-not-exist")


def test_recognize_file_crops_to_the_bbox(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.new("RGB", (100, 80), "white").save(path)
    engine = _SpyEngine()

    result = recognize_file(path, BBox(x=10, y=20, width=30, height=15), engine)

    # The engine received exactly the cropped region (height, width, channels).
    assert engine.last_shape == (15, 30, 3)
    assert result.text == "30x15"


def test_recognize_file_clamps_out_of_bounds_bbox(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.new("RGB", (40, 40), "white").save(path)
    engine = _SpyEngine()

    # A bbox running past the edge is clamped to the image bounds, never erroring.
    recognize_file(path, BBox(x=30, y=30, width=50, height=50), engine)
    assert engine.last_shape == (10, 10, 3)


@pytest.mark.skipif(_MANGA_OCR_INSTALLED, reason="manga-ocr is installed; can't test its absence")
def test_manga_ocr_reports_missing_dependency_clearly() -> None:
    image = np.full((10, 10, 3), 255, dtype=np.uint8)
    with pytest.raises(OcrDependencyError, match="pip install"):
        MangaOcrEngine().recognize(image)
