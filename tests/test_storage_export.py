"""Tests for compositing translated pages and bundling the export (§7.6; FR-43, MVP-9; NFR-26)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from mfo.core import (
    OCRSpan,
    Page,
    Project,
    Region,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.enums import CandidateKind, RegionType
from mfo.core.geometry import BBox
from mfo.render import Placement, composite_file, get_preset, mask_file
from mfo.storage import (
    RENDER_KIND,
    PagePlacement,
    ProjectStore,
    composite_pages,
    export_pages,
    import_pages,
    mask_pages,
    page_placements,
    translate_units,
)
from mfo.storage.hashing import sha256_file
from mfo.vision.ingest import discover_images

_MASK_SIGNATURE = "mask@1;pad=2;border=4"
_COMPOSITE_SIGNATURE = "composite@1"


@dataclass(frozen=True)
class _Result:
    text: str
    confidence: float | None = None


def _echo(source: str, context: dict[str, object]) -> _Result:
    return _Result(text=f"EN[{source}]", confidence=0.8)


def _mask(path: Path, boxes: list[BBox]) -> object:
    return mask_file(path, boxes)


def _composite(base_path: Path, placements: list[PagePlacement]) -> object:
    return composite_file(
        base_path,
        [Placement(text=p.text, box=p.bbox, preset=get_preset(p.preset)) for p in placements],
    )


def _project_with_unit(root: Path, source: Path) -> ProjectStore:
    """A project with one imported page carrying a single translated bubble unit."""
    source.mkdir()
    arr = np.full((100, 100, 3), 255, dtype=np.uint8)
    arr[15:40, 15:70] = 0  # a text block to be masked
    Image.fromarray(arr, mode="RGB").save(source / "p1.png")

    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    page = store.db.list(Page)[0]

    region = Region(
        page_id=page.id,
        bbox=BBox(x=15, y=15, width=55, height=25),
        reading_order_index=0,
        type=RegionType.BUBBLE,
    )
    store.db.save(region)
    store.db.save(OCRSpan(region_id=region.id, text="こんにちは", confidence=0.9))
    store.db.save(TranslationUnit(page_id=page.id, ordered_region_ids=[region.id]))
    translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
    return store


def test_page_placements_builds_one_per_translated_unit(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        placements = page_placements(store, page)

        assert len(placements) == 1
        assert placements[0].text == "EN[こんにちは]"  # the selected translation (I-3)
        assert placements[0].preset == "default"  # bubble → default preset (FR-35)
        assert placements[0].bbox == BBox(x=15, y=15, width=55, height=25)


def test_composite_pages_writes_render_traced_to_page(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        original = store.layout.root / page.image_path
        before = sha256_file(original)
        mask_pages(store, mask=_mask, signature=_MASK_SIGNATURE)
        created = composite_pages(store, composite=_composite, signature=_COMPOSITE_SIGNATURE)

        assert len(created) == 1
        artifact = created[0]
        assert artifact.page_id == page.id  # render traces to its source page (I-2)
        assert artifact.params["kind"] == RENDER_KIND
        assert artifact.params["placements"] == 1

        render_path = store.layout.root / artifact.output_path
        assert render_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        # Compositing onto the masked layer leaves the original untouched (I-1).
        assert sha256_file(original) == before


def test_composite_pages_falls_back_to_original_without_a_mask(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        # No mask_pages run: composite straight onto the original page.
        created = composite_pages(store, composite=_composite, signature=_COMPOSITE_SIGNATURE)
        assert len(created) == 1
        assert created[0].params["kind"] == RENDER_KIND


def test_composite_pages_idempotent_then_invalidated_by_retranslation(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        first = composite_pages(store, composite=_composite, signature=_COMPOSITE_SIGNATURE)
        assert len(first) == 1
        # Unchanged inputs → skipped, exactly one render artifact remains (no duplicates).
        assert composite_pages(store, composite=_composite, signature=_COMPOSITE_SIGNATURE) == []
        renders = [
            a
            for a in store.db.list(RenderArtifact, where=("page_id", page.id))
            if a.params["kind"] == RENDER_KIND
        ]
        assert len(renders) == 1

        # Change the selected translation (a human edit, I-3) → the render must recompute.
        unit = store.db.list(TranslationUnit, where=("page_id", page.id))[0]
        manual = TranslationCandidate(text="Hi there!", kind=CandidateKind.MANUAL)
        store.db.save(
            unit.model_copy(
                update={
                    "candidates": [*unit.candidates, manual],
                    "selected_candidate_id": manual.id,
                }
            )
        )
        assert (
            len(composite_pages(store, composite=_composite, signature=_COMPOSITE_SIGNATURE)) == 1
        )


def test_export_pages_bundles_pages_mapping_manifest_transcript(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        mask_pages(store, mask=_mask, signature=_MASK_SIGNATURE)
        composite_pages(store, composite=_composite, signature=_COMPOSITE_SIGNATURE)

        out_dir = tmp_path / "out"
        result = export_pages(store, out_dir)

        assert len(result.pages) == 1
        exported = result.pages[0]
        assert exported.source == "render"  # exported the composited page
        assert (out_dir / exported.file).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

        # DoD: mapping JSON for the volume.
        mapping = json.loads(result.mapping_path.read_text(encoding="utf-8"))
        assert mapping["units"][0]["translation"] == "EN[こんにちは]"

        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["export_version"] == 1
        assert manifest["pages"][0]["file"] == "pages/0000.png"
        assert manifest["pages"][0]["source"] == "render"

        transcript = result.transcript_path.read_text(encoding="utf-8")
        assert "こんにちは" in transcript
        assert "EN[こんにちは]" in transcript


def test_export_pages_falls_back_to_original_when_unrendered(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        out_dir = tmp_path / "out"
        result = export_pages(store, out_dir)

        # Nothing masked or composited yet → the original page is exported.
        assert result.pages[0].source == "original"
        assert (out_dir / result.pages[0].file).is_file()


def test_export_is_deterministic(tmp_path: Path) -> None:
    with _project_with_unit(tmp_path / "proj", tmp_path / "src") as store:
        mask_pages(store, mask=_mask, signature=_MASK_SIGNATURE)
        composite_pages(store, composite=_composite, signature=_COMPOSITE_SIGNATURE)

        first = export_pages(store, tmp_path / "a")
        second = export_pages(store, tmp_path / "b")
        assert (first.out_dir / first.pages[0].file).read_bytes() == (
            second.out_dir / second.pages[0].file
        ).read_bytes()  # NFR-26
        assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
