"""Bring discovered source images into a project as ``Page`` records (FR-1, FR-4; §10.1).

Originals are *copied* (never moved or modified) into the project's ``pages/`` directory so the
project is self-contained and source files stay untouched (invariants I-1, FR-3). The operation
is idempotent: images already imported (same ``pages/<name>`` path) are skipped, so an
interrupted import resumes without duplicating pages (FR-5).
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from mfo.core import Page
from mfo.storage.project import ProjectStore


class SourceImage(Protocol):
    """The minimum a discovered image must expose to be imported."""

    @property
    def source_path(self) -> Path: ...

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...


def import_pages(
    store: ProjectStore, images: Sequence[SourceImage], *, copy: bool = True
) -> list[Page]:
    """Copy ``images`` into the project and persist a ``Page`` for each new one."""
    existing = store.db.list(Page)
    existing_paths = {page.image_path for page in existing}
    next_index = max((page.index for page in existing), default=-1) + 1

    pages_dir = store.layout.pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)

    new_pages: list[Page] = []
    for image in images:
        relative_path = f"pages/{image.source_path.name}"
        if relative_path in existing_paths:
            continue
        if copy:
            destination = pages_dir / image.source_path.name
            if not destination.exists():
                shutil.copy2(image.source_path, destination)
        new_pages.append(
            Page(
                project_id=store.project.id,
                index=next_index,
                image_path=relative_path,
                width=image.width,
                height=image.height,
            )
        )
        existing_paths.add(relative_path)
        next_index += 1

    store.db.save_all(new_pages)
    return new_pages
