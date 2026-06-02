"""Tests for ProjectStore: create/open/save and directory layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from mfo.core import Page, Project
from mfo.storage.hashing import content_key
from mfo.storage.layout import SUBDIRS
from mfo.storage.project import ProjectStore


def _project() -> Project:
    return Project(name="vol1", source_lang="ja", target_lang="en")


def test_create_then_open_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    project = _project()
    with ProjectStore.create(root, project) as store:
        store.db.save(Page(project_id=project.id, index=0, image_path="a.png", width=1, height=1))

    # All standard subdirectories were created.
    for name in SUBDIRS:
        assert (root / name).is_dir()

    with ProjectStore.open(root) as store:
        assert store.project == project
        assert len(store.db.list(Page)) == 1


def test_create_refuses_existing_project(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    ProjectStore.create(root, _project()).close()
    with pytest.raises(FileExistsError):
        ProjectStore.create(root, _project())


def test_open_missing_project_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ProjectStore.open(tmp_path / "nope")


def test_set_project_persists(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    project = _project()
    with ProjectStore.create(root, project) as store:
        store.set_project(project.model_copy(update={"target_lang": "fr"}))
    with ProjectStore.open(root) as store:
        assert store.project.target_lang == "fr"


def test_cache_is_rooted_in_project(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    with ProjectStore.create(root, _project()) as store:
        key = content_key("k")
        store.cache.write_bytes(key, b"x")
        assert store.cache.get(key) == b"x"
        assert store.cache.path_for(key).is_relative_to(root / "cache")
