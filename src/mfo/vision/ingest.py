"""Directory import: discover page images and put them in reading order (FR-1, FR-2; §10.1).

This module is pure and storage-free: it scans a source directory, applies an ordering
strategy, and reads each image's dimensions, returning a plain :class:`ImportScan`. Copying
files into the project and creating ``Page`` records is the storage layer's job
(:func:`mfo.storage.ingest.import_pages`), so discovery stays easy to test in isolation.

Malformed images and manifest entries with no matching file are collected as skips rather than
aborting the whole import (NFR-9).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from mfo.vision.images import ImageError, is_supported, read_image_size


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


def discover_images(
    source_dir: Path,
    *,
    order: PageOrder = PageOrder.NATURAL,
    manifest_order: Sequence[str] | None = None,
    reader: Callable[[Path], tuple[int, int]] = read_image_size,
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
