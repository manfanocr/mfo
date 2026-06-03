"""Bundle a finished project into a portable export (spec §7.6; FR-43, MVP-9, NFR-26).

The render stage composites each page into ``renders/<page>.render.png``; this gathers those into a
self-contained export directory: the translated page images, the full source → OCR → translation
**mapping** (:mod:`mfo.storage.mapping`), a machine-readable **manifest** describing the export, and
a human-readable **transcript** of every unit's source and translation. A page with no composited
render (e.g. nothing translated on it yet) falls back to its masked layer, then to the original
image, so the export always covers the whole volume. Output is ordered by page index and otherwise
deterministic, so re-exporting an unchanged project reproduces the same bytes (NFR-26).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mfo.core import OCRSpan, Page, Region, RenderArtifact, TranslationUnit, selected_text
from mfo.storage.atomic import atomic_write_bytes, atomic_write_text
from mfo.storage.mapping import write_mapping
from mfo.storage.project import ProjectStore
from mfo.storage.render import MASK_KIND, RENDER_KIND, _unit_sort_key

# Bumped when the export manifest schema changes incompatibly.
EXPORT_VERSION = 1


@dataclass(frozen=True)
class ExportedPage:
    """One page in an export: where it came from and the file written."""

    index: int
    page_id: str
    source: str  # "render", "masked", or "original"
    file: str  # path relative to the export directory
    overflow: int


@dataclass(frozen=True)
class ExportResult:
    """The outcome of an export: the directory and the artifacts written into it."""

    out_dir: Path
    pages: tuple[ExportedPage, ...]
    mapping_path: Path
    manifest_path: Path
    transcript_path: Path

    @property
    def overflow(self) -> int:
        """Total placements that overflowed their box across the export (I-4)."""
        return sum(page.overflow for page in self.pages)


def _page_source(store: ProjectStore, page: Page) -> tuple[Path, str, int]:
    """Pick the best available layer to export for a page: render → masked → original."""
    artifacts = store.db.list(RenderArtifact, where=("page_id", page.id))
    by_kind = {a.params.get("kind"): a for a in artifacts}
    render = by_kind.get(RENDER_KIND)
    if render is not None:
        overflow = int(render.params.get("overflow", 0))
        return store.layout.root / render.output_path, "render", overflow
    masked = by_kind.get(MASK_KIND)
    if masked is not None:
        return store.layout.root / masked.output_path, "masked", 0
    return store.layout.root / page.image_path, "original", 0


def _write_transcript(store: ProjectStore, path: Path) -> None:
    """Write a human-readable source → translation transcript, in page and reading order."""
    project = store.project
    lines = [f"# {project.name}  ({project.source_lang} -> {project.target_lang})", ""]
    for page in store.db.list(Page, order_by="idx"):
        lines.append(f"## Page {page.index + 1}")
        units = store.db.list(TranslationUnit, where=("page_id", page.id))
        regions = {r.id: r for r in store.db.list(Region, where=("page_id", page.id))}
        for n, unit in enumerate(sorted(units, key=lambda u: _unit_sort_key(u, regions)), start=1):
            source = " ".join(
                span.text
                for rid in unit.ordered_region_ids
                for span in store.db.list(OCRSpan, where=("region_id", rid))
            )
            lines.append(f"[{n}] {source or unit.source_bundle}  ->  {selected_text(unit)}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines).rstrip("\n") + "\n")


def export_pages(store: ProjectStore, out_dir: Path) -> ExportResult:
    """Export the translated pages, mapping, manifest, and transcript into ``out_dir`` (FR-43)."""
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    project = store.project
    exported: list[ExportedPage] = []
    for page in store.db.list(Page, order_by="idx"):
        source_path, source_kind, overflow = _page_source(store, page)
        rel = f"pages/{page.index:04d}.png"
        atomic_write_bytes(out_dir / rel, source_path.read_bytes())
        exported.append(
            ExportedPage(
                index=page.index,
                page_id=page.id,
                source=source_kind,
                file=rel,
                overflow=overflow,
            )
        )

    mapping_path = write_mapping(store, out_dir / "mapping.json")
    transcript_path = out_dir / "transcript.txt"
    _write_transcript(store, transcript_path)

    manifest_path = out_dir / "manifest.json"
    manifest = {
        "export_version": EXPORT_VERSION,
        "project": {
            "id": project.id,
            "name": project.name,
            "source_lang": project.source_lang,
            "target_lang": project.target_lang,
            "reading_direction": project.reading_direction.value,
        },
        "pages": [
            {
                "index": page.index,
                "page_id": page.page_id,
                "source": page.source,
                "file": page.file,
                "overflow": page.overflow,
            }
            for page in exported
        ],
        "overflow": sum(page.overflow for page in exported),
        "mapping": "mapping.json",
        "transcript": "transcript.txt",
    }
    atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    return ExportResult(
        out_dir=out_dir,
        pages=tuple(exported),
        mapping_path=mapping_path,
        manifest_path=manifest_path,
        transcript_path=transcript_path,
    )
