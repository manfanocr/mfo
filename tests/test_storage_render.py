"""Tests for persisting masked page layers (§10.8; FR-31/32/33; I-1/I-2/I-6; NFR-8)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from mfo.core import (
    Page,
    Project,
    Region,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.enums import RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.render import mask_file
from mfo.storage import ProjectStore, import_pages, mask_pages
from mfo.storage.hashing import sha256_file
from mfo.storage.render import page_placements
from mfo.vision.ingest import discover_images

_SIGNATURE = "mask@1;pad=2;border=4"


def _mask(path: Path, boxes: list[BBox]) -> object:
    return mask_file(path, boxes)


def _project_with_page(root: Path, source: Path) -> ProjectStore:
    source.mkdir()
    arr = np.full((40, 40, 3), 255, dtype=np.uint8)
    arr[15:25, 15:25] = 0  # text block to be removed
    Image.fromarray(arr, mode="RGB").save(source / "p1.png")
    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    import_pages(store, discover_images(source).images)
    return store


def _add_region(store: ProjectStore, page: Page, box: BBox) -> Region:
    region = Region(page_id=page.id, bbox=box, reading_order_index=0)
    store.db.save(region)
    return region


def test_mask_pages_writes_layers_and_traces_to_page(tmp_path: Path) -> None:
    with _project_with_page(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        original = store.layout.root / page.image_path
        before = sha256_file(original)
        _add_region(store, page, BBox(x=15, y=15, width=10, height=10))

        artifacts = mask_pages(store, mask=_mask, signature=_SIGNATURE)

        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.page_id == page.id  # output traces back to its source page (I-2)

        masked_path = store.layout.root / artifact.output_path
        mask_path = store.layout.root / artifact.params["mask_path"]
        assert masked_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert mask_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

        # The text block was removed in the masked layer (FR-31).
        masked_arr = np.asarray(Image.open(masked_path).convert("RGB"))
        assert np.all(masked_arr[15:25, 15:25] == 255)

        # Original is untouched (I-1, FR-3).
        assert sha256_file(original) == before


def test_mask_pages_without_regions_copies_the_page(tmp_path: Path) -> None:
    with _project_with_page(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]

        artifacts = mask_pages(store, mask=_mask, signature=_SIGNATURE)

        # A region-less page still gets a masked base for downstream rendering.
        assert len(artifacts) == 1
        masked = np.asarray(Image.open(store.layout.root / artifacts[0].output_path).convert("RGB"))
        original = np.asarray(Image.open(store.layout.root / page.image_path).convert("RGB"))
        assert np.array_equal(masked, original)


def test_mask_pages_is_idempotent_then_forced(tmp_path: Path) -> None:
    with _project_with_page(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        _add_region(store, page, BBox(x=15, y=15, width=10, height=10))

        first = mask_pages(store, mask=_mask, signature=_SIGNATURE)
        assert len(first) == 1
        # Unchanged inputs → skipped, and exactly one artifact remains (no duplicates).
        assert mask_pages(store, mask=_mask, signature=_SIGNATURE) == []
        assert len(store.db.list(RenderArtifact, where=("page_id", page.id))) == 1

        forced = mask_pages(store, mask=_mask, signature=_SIGNATURE, force=True)
        assert len(forced) == 1
        assert len(store.db.list(RenderArtifact, where=("page_id", page.id))) == 1


def _add_unit(store: ProjectStore, page: Page, region: Region, text: str) -> None:
    cand = TranslationCandidate(text=text)
    store.db.save(
        TranslationUnit(
            page_id=page.id,
            ordered_region_ids=[region.id],
            candidates=[cand],
            selected_candidate_id=cand.id,
        )
    )


def test_page_placements_are_per_bubble(tmp_path: Path) -> None:
    # With one unit per bubble, two close bubbles render as two placements in their own boxes —
    # text no longer overflows a merged union box (items 8/10).
    with _project_with_page(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        top = Region(page_id=page.id, bbox=BBox(x=2, y=2, width=8, height=8), reading_order_index=0)
        bottom = Region(
            page_id=page.id, bbox=BBox(x=2, y=12, width=8, height=8), reading_order_index=1
        )
        store.db.save_all([top, bottom])
        _add_unit(store, page, top, "first")
        _add_unit(store, page, bottom, "second")

        placements = page_placements(store, page)
        assert [p.text for p in placements] == ["first", "second"]
        # Each placement keeps its own bubble box, not the union of both.
        assert placements[0].bbox == top.bbox
        assert placements[1].bbox == bottom.bbox


def test_page_placements_skips_ignored_regions(tmp_path: Path) -> None:
    # An auto-ignored panel/frame region must not be typeset, even if a unit carries text (item 11).
    with _project_with_page(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        good = Region(
            page_id=page.id, bbox=BBox(x=2, y=2, width=8, height=8), reading_order_index=0
        )
        ignored = Region(
            page_id=page.id,
            bbox=BBox(x=15, y=15, width=20, height=20),
            type=RegionType.BUBBLE,
            reading_order_index=1,
            status=RegionStatus.IGNORE,
        )
        store.db.save_all([good, ignored])
        _add_unit(store, page, good, "kept")
        _add_unit(store, page, ignored, "dropped")

        placements = page_placements(store, page)
        assert [p.text for p in placements] == ["kept"]


def test_redetecting_regions_invalidates_the_mask(tmp_path: Path) -> None:
    with _project_with_page(tmp_path / "proj", tmp_path / "src") as store:
        page = store.db.list(Page)[0]
        region = _add_region(store, page, BBox(x=15, y=15, width=10, height=10))
        mask_pages(store, mask=_mask, signature=_SIGNATURE)

        # Move the region (as a re-detection would): the mask must recompute.
        store.db.save(region.model_copy(update={"bbox": BBox(x=5, y=5, width=8, height=8)}))
        assert len(mask_pages(store, mask=_mask, signature=_SIGNATURE)) == 1
