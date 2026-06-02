"""Tests for copying discovered images into a project as Pages (FR-1, FR-3, FR-4, I-1)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from mfo.core import Page, Project
from mfo.storage import ProjectStore, import_pages
from mfo.vision.ingest import discover_images


def _make_png(path: Path, size: tuple[int, int] = (4, 5)) -> None:
    Image.new("RGB", size, "white").save(path)


def _project(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def test_import_creates_pages_and_copies_originals(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "p1.png", size=(4, 5))
    _make_png(source / "p2.png", size=(6, 7))

    with _project(tmp_path / "proj") as store:
        scan = discover_images(source)
        pages = import_pages(store, scan.images)

        assert [(p.index, p.image_path) for p in pages] == [
            (0, "pages/p1.png"),
            (1, "pages/p2.png"),
        ]
        assert (pages[1].width, pages[1].height) == (6, 7)
        # Files copied into the project, originals untouched (I-1, FR-3).
        assert (store.layout.pages_dir / "p1.png").is_file()
        assert (source / "p1.png").is_file()
        # Persisted and queryable.
        assert len(store.db.list(Page)) == 2


def test_import_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "p1.png")

    with _project(tmp_path / "proj") as store:
        scan = discover_images(source)
        first = import_pages(store, scan.images)
        second = import_pages(store, discover_images(source).images)
        assert len(first) == 1
        assert second == []  # nothing new on re-import
        assert len(store.db.list(Page)) == 1


def test_resume_appends_new_pages_with_continuing_index(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "p1.png")

    with _project(tmp_path / "proj") as store:
        import_pages(store, discover_images(source).images)
        _make_png(source / "p2.png")
        new = import_pages(store, discover_images(source).images)
        assert [(p.index, p.image_path) for p in new] == [(1, "pages/p2.png")]
        assert {p.index for p in store.db.list(Page)} == {0, 1}
