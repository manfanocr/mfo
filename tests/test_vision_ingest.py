"""Tests for directory discovery and page ordering (FR-1, FR-2, NFR-9, MVP-1, MVP-2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from mfo.vision.ingest import PageOrder, discover_images


def _make_png(path: Path, size: tuple[int, int] = (2, 3)) -> None:
    Image.new("RGB", size, "white").save(path)


def test_natural_order_sorts_numerically(tmp_path: Path) -> None:
    for name in ("page1.png", "page2.png", "page10.png"):
        _make_png(tmp_path / name)
    scan = discover_images(tmp_path, order=PageOrder.NATURAL)
    assert [img.source_path.name for img in scan.images] == ["page1.png", "page2.png", "page10.png"]


def test_name_order_is_lexicographic(tmp_path: Path) -> None:
    for name in ("page1.png", "page2.png", "page10.png"):
        _make_png(tmp_path / name)
    scan = discover_images(tmp_path, order=PageOrder.NAME)
    assert [img.source_path.name for img in scan.images] == ["page1.png", "page10.png", "page2.png"]


def test_dimensions_are_captured(tmp_path: Path) -> None:
    _make_png(tmp_path / "a.png", size=(7, 11))
    scan = discover_images(tmp_path)
    assert (scan.images[0].width, scan.images[0].height) == (7, 11)


def test_unsupported_files_ignored(tmp_path: Path) -> None:
    _make_png(tmp_path / "a.png")
    (tmp_path / "notes.txt").write_text("not an image")
    scan = discover_images(tmp_path)
    assert [img.source_path.name for img in scan.images] == ["a.png"]


def test_mixed_case_suffixes_supported(tmp_path: Path) -> None:
    _make_png(tmp_path / "a.PNG")
    _make_png(tmp_path / "b.JpG")
    scan = discover_images(tmp_path)
    assert {img.source_path.name for img in scan.images} == {"a.PNG", "b.JpG"}


def test_corrupt_image_is_skipped_not_fatal(tmp_path: Path) -> None:
    _make_png(tmp_path / "good.png")
    (tmp_path / "bad.png").write_bytes(b"not a real png")
    scan = discover_images(tmp_path)
    assert [img.source_path.name for img in scan.images] == ["good.png"]
    assert [skip.source_path.name for skip in scan.skipped] == ["bad.png"]
    assert "bad.png" in scan.skipped[0].reason


def test_manifest_order_overrides(tmp_path: Path) -> None:
    for name in ("a.png", "b.png", "c.png"):
        _make_png(tmp_path / name)
    scan = discover_images(tmp_path, order=PageOrder.MANIFEST, manifest_order=["c.png", "a.png"])
    # Listed files come first in manifest order; unlisted files follow in natural order.
    assert [img.source_path.name for img in scan.images] == ["c.png", "a.png", "b.png"]


def test_manifest_missing_entry_is_skipped(tmp_path: Path) -> None:
    _make_png(tmp_path / "a.png")
    scan = discover_images(
        tmp_path, order=PageOrder.MANIFEST, manifest_order=["a.png", "ghost.png"]
    )
    assert [img.source_path.name for img in scan.images] == ["a.png"]
    assert [skip.source_path.name for skip in scan.skipped] == ["ghost.png"]


def test_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        discover_images(tmp_path / "nope")
