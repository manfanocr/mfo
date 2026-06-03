"""Source import: discover page images and put them in reading order (FR-1, FR-2; §10.1).

This module is pure and storage-free: it scans a source — a directory **or** a ``.cbz``/``.zip``
archive — applies an ordering strategy, and reads each image's dimensions, returning a plain
:class:`ImportScan`. Copying files into the project and creating ``Page`` records is the storage
layer's job (:func:`mfo.storage.ingest.import_pages`), so discovery stays easy to test in isolation.

Archives are read-only: their images are extracted into a caller-supplied staging directory (the
project cache) and the source archive itself is never modified (invariant I-1). CBR/RAR is out of
scope — it needs a non-free dependency.

Malformed images, corrupt archive entries, and manifest entries with no matching file are collected
as skips rather than aborting the whole import (NFR-9).
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from mfo.vision.images import ImageError, is_supported, read_image_size

# Container formats we can read page images out of (FR-1). Matched case-insensitively. A ``.cbz``
# is just a ZIP of images, so both are handled by :mod:`zipfile`.
ARCHIVE_SUFFIXES = frozenset({".cbz", ".zip"})


def is_archive(path: Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_SUFFIXES


class PageOrder(StrEnum):
    NATURAL = "natural"  # numeric-aware: page2 < page10
    NAME = "name"  # plain lexicographic
    MANIFEST = "manifest"  # explicit filename order


@dataclass(frozen=True)
class DiscoveredImage:
    source_path: Path
    width: int
    height: int


@dataclass(frozen=True)
class SkippedImage:
    source_path: Path
    reason: str


@dataclass(frozen=True)
class ImportScan:
    images: list[DiscoveredImage]
    skipped: list[SkippedImage]


def _natural_key(name: str) -> tuple[int | str, ...]:
    """Split ``name`` into text/number chunks so that e.g. ``1, 2, 10`` sort numerically.

    ``re.split`` on ``(\\d+)`` yields chunks that always alternate text, number, text, …, so a
    given position holds the same type across names — the resulting tuples compare safely.
    """
    return tuple(
        int(chunk) if chunk.isdigit() else chunk.lower() for chunk in re.split(r"(\d+)", name)
    )


def extract_archive(archive_path: Path, dest_dir: Path) -> list[SkippedImage]:
    """Extract the supported page images from a CBZ/ZIP into ``dest_dir`` (read-only source, I-1).

    Entries are flattened to their basenames (CBZ/ZIP volumes are conventionally flat) and written
    under ``dest_dir`` — only the basename is ever used, so a malicious archive cannot escape it
    (no zip-slip). Non-image entries (``ComicInfo.xml``, ``Thumbs.db``, AppleDouble ``._*`` files,
    ``__MACOSX/`` resource forks) are silently ignored; a corrupt entry or a basename collision is
    recorded as a skip rather than aborting the whole import (NFR-9). A wholly unreadable archive
    raises :class:`ImageError`.
    """
    archive_path = Path(archive_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ImageError(f"cannot read archive {archive_path.name}: {exc}") from exc

    skipped: list[SkippedImage] = []
    seen: set[str] = set()
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member = PurePosixPath(info.filename)
            name = member.name
            if name.startswith(".") or "__MACOSX" in member.parts:
                continue  # hidden / AppleDouble / resource-fork noise
            if not is_supported(Path(name)):
                continue  # non-image entry (ComicInfo.xml, etc.)
            if name in seen:
                skipped.append(SkippedImage(Path(info.filename), "duplicate name in archive"))
                continue
            seen.add(name)
            try:
                data = archive.read(info)
            except (zipfile.BadZipFile, OSError) as exc:
                skipped.append(SkippedImage(Path(info.filename), f"corrupt archive entry: {exc}"))
                continue
            (dest_dir / name).write_bytes(data)
    return skipped


def discover_images(
    source: Path,
    *,
    order: PageOrder = PageOrder.NATURAL,
    manifest_order: Sequence[str] | None = None,
    reader: Callable[[Path], tuple[int, int]] = read_image_size,
    extract_to: Path | None = None,
) -> ImportScan:
    """Discover ordered page images from a directory or a CBZ/ZIP archive, recording skips.

    For an archive, ``extract_to`` (a staging directory, e.g. the project cache) is required: the
    archive's images are extracted there read-only and then discovered like a directory.
    """
    source = Path(source)
    if is_archive(source):
        if extract_to is None:
            raise ValueError(f"extract_to is required to import the archive {source.name}")
        extract_skips = extract_archive(source, extract_to)
        scan = _discover_directory(
            extract_to, order=order, manifest_order=manifest_order, reader=reader
        )
        return ImportScan(images=scan.images, skipped=[*extract_skips, *scan.skipped])
    return _discover_directory(source, order=order, manifest_order=manifest_order, reader=reader)


def _discover_directory(
    source_dir: Path,
    *,
    order: PageOrder,
    manifest_order: Sequence[str] | None,
    reader: Callable[[Path], tuple[int, int]],
) -> ImportScan:
    """Scan ``source_dir`` for supported images and return them ordered, with skips recorded."""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise NotADirectoryError(f"not a directory: {source_dir}")

    files = [p for p in source_dir.iterdir() if p.is_file() and is_supported(p)]
    skipped: list[SkippedImage] = []

    if order is PageOrder.MANIFEST:
        ordered, manifest_skips = _apply_manifest(source_dir, files, manifest_order or [])
        skipped.extend(manifest_skips)
    elif order is PageOrder.NAME:
        ordered = sorted(files, key=lambda p: p.name)
    else:
        ordered = sorted(files, key=lambda p: _natural_key(p.name))

    images: list[DiscoveredImage] = []
    for path in ordered:
        try:
            width, height = reader(path)
        except ImageError as exc:
            skipped.append(SkippedImage(path, str(exc)))
            continue
        images.append(DiscoveredImage(path, width, height))

    return ImportScan(images=images, skipped=skipped)


def _apply_manifest(
    source_dir: Path, files: list[Path], manifest_order: Sequence[str]
) -> tuple[list[Path], list[SkippedImage]]:
    """Order ``files`` by ``manifest_order``; unlisted files follow in natural order."""
    by_name = {p.name: p for p in files}
    ordered: list[Path] = []
    skipped: list[SkippedImage] = []
    for name in manifest_order:
        path = by_name.pop(name, None)
        if path is None:
            skipped.append(SkippedImage(source_dir / name, "listed in manifest but not found"))
        else:
            ordered.append(path)
    ordered.extend(sorted(by_name.values(), key=lambda p: _natural_key(p.name)))
    return ordered, skipped
