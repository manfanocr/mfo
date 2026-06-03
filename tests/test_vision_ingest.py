"""Tests for source discovery and page ordering (FR-1, FR-2, NFR-9, MVP-1, MVP-2)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from mfo.vision.images import ImageError
from mfo.vision.ingest import (
    PageOrder,
    discover_images,
    extract_archive,
    is_archive,
)


def _make_png(path: Path, size: tuple[int, int] = (2, 3)) -> None:
    Image.new("RGB", size, "white").save(path)


def _png_bytes(size: tuple[int, int] = (2, 3)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _make_cbz(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


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


# --- archive (CBZ/ZIP) import (FR-1, FR-2, I-1, NFR-9; relaxes NG-4) ---


def test_is_archive_recognizes_cbz_and_zip() -> None:
    assert is_archive(Path("vol.cbz"))
    assert is_archive(Path("vol.ZIP"))
    assert not is_archive(Path("vol.cbr"))
    assert not is_archive(Path("pages"))


def test_archive_import_orders_pages_naturally(tmp_path: Path) -> None:
    archive = tmp_path / "vol.cbz"
    _make_cbz(
        archive,
        {"page1.png": _png_bytes((7, 11)), "page2.png": _png_bytes(), "page10.png": _png_bytes()},
    )
    scan = discover_images(archive, extract_to=tmp_path / "stage")
    assert [img.source_path.name for img in scan.images] == ["page1.png", "page2.png", "page10.png"]
    assert (scan.images[0].width, scan.images[0].height) == (7, 11)
    # Source archive untouched (I-1).
    assert archive.is_file()


def test_archive_import_ignores_non_image_entries(tmp_path: Path) -> None:
    archive = tmp_path / "vol.zip"
    _make_cbz(
        archive,
        {
            "001.png": _png_bytes(),
            "ComicInfo.xml": b"<ComicInfo/>",
            "__MACOSX/._001.png": b"junk",
            ".hidden.png": b"junk",
        },
    )
    scan = discover_images(archive, extract_to=tmp_path / "stage")
    assert [img.source_path.name for img in scan.images] == ["001.png"]
    assert scan.skipped == []


def test_archive_corrupt_entry_is_skipped_not_fatal(tmp_path: Path) -> None:
    archive = tmp_path / "vol.cbz"
    _make_cbz(archive, {"good.png": _png_bytes(), "bad.png": b"not a real png"})
    scan = discover_images(archive, extract_to=tmp_path / "stage")
    # The bad entry extracts fine (it's only "corrupt" as an image) but fails dimension reading.
    assert [img.source_path.name for img in scan.images] == ["good.png"]
    assert [skip.source_path.name for skip in scan.skipped] == ["bad.png"]


def test_archive_duplicate_basename_is_skipped(tmp_path: Path) -> None:
    archive = tmp_path / "vol.cbz"
    _make_cbz(archive, {"a/1.png": _png_bytes(), "b/1.png": _png_bytes((5, 5))})
    skipped = extract_archive(archive, tmp_path / "stage")
    assert [skip.source_path.as_posix() for skip in skipped] == ["b/1.png"]
    assert [skip.reason for skip in skipped] == ["duplicate name in archive"]
    assert (tmp_path / "stage" / "1.png").is_file()


def test_unreadable_archive_raises_image_error(tmp_path: Path) -> None:
    archive = tmp_path / "vol.cbz"
    archive.write_bytes(b"not a zip at all")
    with pytest.raises(ImageError):
        discover_images(archive, extract_to=tmp_path / "stage")


def test_archive_without_extract_dir_raises(tmp_path: Path) -> None:
    archive = tmp_path / "vol.cbz"
    _make_cbz(archive, {"001.png": _png_bytes()})
    with pytest.raises(ValueError, match="extract_to"):
        discover_images(archive)
