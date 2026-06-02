"""Tests for caching preprocessing derivatives and persisting metadata (§10.2, I-1, NFR-7/8)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from mfo.core import Page, Project
from mfo.storage import ProjectStore, import_pages, preprocess_pages
from mfo.storage.hashing import sha256_file
from mfo.vision.ingest import discover_images
from mfo.vision.preprocess import PreprocessConfig, preprocess_file


def _project_with_page(root: Path, source: Path) -> ProjectStore:
    source.mkdir()
    Image.new("RGB", (12, 8), "white").save(source / "p1.png")
    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    return store


def _transform(
    config: PreprocessConfig,
) -> Callable[[Path], tuple[bytes, dict[str, Any]]]:
    return lambda path: preprocess_file(path, config)


def test_writes_derivative_to_cache_and_records_metadata(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    config = PreprocessConfig(max_dimension=6)
    original = store.layout.pages_dir / "p1.png"
    before = sha256_file(original)

    with store:
        pages = preprocess_pages(store, transform=_transform(config), signature=config.signature())
        assert len(pages) == 1
        meta = pages[0].preprocessing
        assert meta["cache_key"]
        assert store.cache.has(meta["cache_key"])
        assert meta["output_size"] == [6, 4]  # downscaled from 12x8
        # Derivative is a valid PNG.
        assert store.cache.read_bytes(meta["cache_key"])[:8] == b"\x89PNG\r\n\x1a\n"

    # Original untouched (I-1, FR-3).
    assert sha256_file(original) == before
    # Metadata persisted across reopen.
    with ProjectStore.open(tmp_path / "proj") as reopened:
        stored = reopened.db.list(Page)[0]
        assert stored.preprocessing["cache_key"] == meta["cache_key"]


def test_idempotent_skips_when_current(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    config = PreprocessConfig()
    calls: list[Path] = []

    def counting(path: Path) -> tuple[bytes, dict[str, Any]]:
        calls.append(path)
        return preprocess_file(path, config)

    with store:
        first = preprocess_pages(store, transform=counting, signature=config.signature())
        second = preprocess_pages(store, transform=counting, signature=config.signature())

    assert len(first) == 1
    assert second == []
    assert len(calls) == 1  # transform not invoked again


def test_force_recomputes(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    config = PreprocessConfig()
    with store:
        preprocess_pages(store, transform=_transform(config), signature=config.signature())
        again = preprocess_pages(
            store, transform=_transform(config), signature=config.signature(), force=True
        )
    assert len(again) == 1


def test_config_change_recomputes(tmp_path: Path) -> None:
    store = _project_with_page(tmp_path / "proj", tmp_path / "src")
    base = PreprocessConfig()
    changed = PreprocessConfig(grayscale=True)
    with store:
        preprocess_pages(store, transform=_transform(base), signature=base.signature())
        rerun = preprocess_pages(
            store, transform=_transform(changed), signature=changed.signature()
        )
    assert len(rerun) == 1  # different signature → new derivative
