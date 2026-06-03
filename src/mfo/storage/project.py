"""ProjectStore: the top-level handle tying together layout, manifest, and database.

This is the entry point the CLI and pipeline use to create, open, and persist a project. It
never destroys an existing project on create (invariant I-1) and keeps the manifest and
database consistent on disk.
"""

from __future__ import annotations

from pathlib import Path

from mfo.core import Project
from mfo.storage.cache import Cache
from mfo.storage.db import Database
from mfo.storage.layout import ProjectLayout
from mfo.storage.manifest import Manifest, read_manifest, write_manifest


class ProjectStore:
    def __init__(self, layout: ProjectLayout, db: Database, manifest: Manifest) -> None:
        self._layout = layout
        self._db = db
        self._manifest = manifest

    @classmethod
    def create(
        cls, root: Path | str, project: Project, *, check_same_thread: bool = True
    ) -> ProjectStore:
        """Create a new project directory. Refuses to overwrite an existing project (I-1)."""
        layout = ProjectLayout.at(root)
        if layout.exists():
            raise FileExistsError(f"a project already exists at {layout.root}")
        layout.ensure()
        manifest = Manifest(project=project)
        write_manifest(layout.manifest_path, manifest)
        db = Database.open(layout.db_path, check_same_thread=check_same_thread)
        return cls(layout, db, manifest)

    @classmethod
    def open(cls, root: Path | str, *, check_same_thread: bool = True) -> ProjectStore:
        """Open an existing project directory.

        Pass ``check_same_thread=False`` when the store will be served by a threaded server (the
        review backend), so SQLite access from worker threads is permitted.
        """
        layout = ProjectLayout.at(root)
        if not layout.exists():
            raise FileNotFoundError(f"no project found at {layout.root}")
        manifest = read_manifest(layout.manifest_path)
        db = Database.open(layout.db_path, check_same_thread=check_same_thread)
        return cls(layout, db, manifest)

    @property
    def layout(self) -> ProjectLayout:
        return self._layout

    @property
    def db(self) -> Database:
        return self._db

    @property
    def cache(self) -> Cache:
        return Cache(self._layout.cache_dir)

    @property
    def project(self) -> Project:
        return self._manifest.project

    def set_project(self, project: Project) -> None:
        """Replace the project header and persist the manifest atomically."""
        self._manifest = Manifest(manifest_version=self._manifest.manifest_version, project=project)
        write_manifest(self._layout.manifest_path, self._manifest)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> ProjectStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
