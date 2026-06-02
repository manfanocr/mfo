"""Human-readable project manifest (spec §11.2, §15).

The manifest holds the :class:`~mfo.core.Project` header in pretty-printed JSON so it is easy
to inspect and diff. Relational data (pages, regions, OCR, translations, edits, renders) lives
in the SQLite database; the manifest owns only the project header.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mfo.core import Project
from mfo.storage.atomic import atomic_write_text

MANIFEST_VERSION = 1


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: int = MANIFEST_VERSION
    project: Project


def read_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(Path(path).read_text(encoding="utf-8"))


def write_manifest(path: Path, manifest: Manifest) -> None:
    atomic_write_text(Path(path), manifest.model_dump_json(indent=2))
