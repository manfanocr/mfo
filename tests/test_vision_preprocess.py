"""Tests for page preprocessing transforms (spec §10.2, FR-3, NFR-5)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from mfo.vision.preprocess import (
    PreprocessConfig,
    detect_orientation,
    estimate_skew_angle,
    preprocess_file,
    preprocess_image,
)


def test_color_normalized_to_rgb_by_default() -> None:
    source = Image.new("L", (8, 8), 128)  # grayscale source
    result, meta = preprocess_image(source, PreprocessConfig())
    assert result.mode == "RGB"
    assert meta["color_mode"] == "RGB"
    assert "color:RGB" in meta["operations"]


def test_grayscale_option() -> None:
    source = Image.new("RGB", (8, 8), "white")
    result, meta = preprocess_image(source, PreprocessConfig(grayscale=True))
    assert result.mode == "L"
    assert meta["color_mode"] == "L"


def test_downscale_records_scale() -> None:
    source = Image.new("RGB", (100, 50), "white")
    result, meta = preprocess_image(source, PreprocessConfig(max_dimension=50))
    assert (result.width, result.height) == (50, 25)
    assert meta["scale"] == 0.5
    assert meta["source_size"] == [100, 50]
    assert meta["output_size"] == [50, 25]


def test_no_downscale_when_within_limit() -> None:
    source = Image.new("RGB", (100, 50), "white")
    result, meta = preprocess_image(source, PreprocessConfig(max_dimension=200))
    assert (result.width, result.height) == (100, 50)
    assert meta["scale"] == 1.0


def test_orientation_detection() -> None:
    assert detect_orientation(100, 50) == "landscape"
    assert detect_orientation(50, 100) == "portrait"
    assert detect_orientation(50, 50) == "square"


def test_denoise_runs_and_is_recorded() -> None:
    source = Image.new("RGB", (16, 16), "white")
    result, meta = preprocess_image(source, PreprocessConfig(denoise=True))
    assert (result.width, result.height) == (16, 16)
    assert "denoise" in meta["operations"]


def _skewed_lines(angle: float, size: int = 80) -> Image.Image:
    """A white image with horizontal black lines, rotated by ``angle`` degrees."""
    image = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(image)
    for y in range(0, size, 6):
        draw.line([(0, y), (size, y)], fill=0, width=1)
    return image.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)


def test_skew_estimator_recovers_correction_angle() -> None:
    skewed = _skewed_lines(5.0)
    angle = estimate_skew_angle(skewed)
    # Content skewed by +5° needs roughly a -5° correction.
    assert abs(angle - (-5.0)) <= 2.0


def test_deskew_records_angle_in_metadata() -> None:
    _, meta = preprocess_image(_skewed_lines(5.0), PreprocessConfig(deskew=True))
    assert "deskew" in meta["operations"]
    assert meta["deskew_angle"] is not None


def test_preprocess_file_returns_png_bytes(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.new("RGB", (10, 12), "white").save(path)
    data, meta = preprocess_file(path, PreprocessConfig())
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert meta["source_size"] == [10, 12]
