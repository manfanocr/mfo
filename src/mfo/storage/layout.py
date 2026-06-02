"""Project directory layout (spec §15).

A project on disk is a directory containing a human-readable ``manifest.json`` header, a
``project.db`` SQLite database for relational/high-churn data, and a fixed set of
subdirectories for pages, caches, per-stage dumps, renders, exports, and logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "manifest.json"
DB_NAME = "project.db"

# Subdirectories created for every project (spec §15).
SUBDIRS = (
    "pages",
    "cache",
    "regions",
    "ocr",
    "translations",
    "renders",
    "exports",
    "logs",
)


@dataclass(frozen=True)
class ProjectLayout:
    """Resolves the canonical paths inside a project directory."""

    root: Path

    @classmethod
    def at(cls, root: Path | str) -> ProjectLayout:
        return cls(Path(root))

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def db_path(self) -> Path:
        return self.root / DB_NAME

    @property
    def pages_dir(self) -> Path:
        return self.root / "pages"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def regions_dir(self) -> Path:
        return self.root / "regions"

    @property
    def ocr_dir(self) -> Path:
        return self.root / "ocr"

    @property
    def translations_dir(self) -> Path:
        return self.root / "translations"

    @property
    def renders_dir(self) -> Path:
        return self.root / "renders"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def pipeline_state_path(self) -> Path:
        """Where the pipeline orchestrator records per-stage completion (for resume)."""
        return self.root / "logs" / "pipeline_state.json"

    def subdirs(self) -> tuple[Path, ...]:
        return tuple(self.root / name for name in SUBDIRS)

    def exists(self) -> bool:
        """A project exists here if its manifest is present."""
        return self.manifest_path.is_file()

    def ensure(self) -> None:
        """Create the project root and all subdirectories if missing."""
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in self.subdirs():
            directory.mkdir(parents=True, exist_ok=True)
