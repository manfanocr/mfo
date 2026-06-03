"""Persist masked page layers for the render stage (spec §10.8; FR-31/32/33; I-1/I-2/I-6, NFR-8/26).

For each page this reads the original image (read-only, I-1), removes the source text within its
regions, and writes two derived layers into the project's ``renders`` dir: ``<page>.masked.png``
(text removed, the base for typesetting) and ``<page>.mask.png`` (a 1-channel record of what
changed, so masking is reversible — I-6). A :class:`RenderArtifact` row links each masked layer
back to its page (I-2). Like the other stages the imaging is *injected* (the render layer supplies
it) so storage stays free of any image dependency.

Each page records a signature folding its source image, the mask config, and a fingerprint of its
regions, so re-running skips unchanged pages (NFR-8) and a re-detection (which moves the regions)
correctly invalidates the mask. A recompute drops the prior mask artifact and its files first, so
masking is idempotent and never leaves stale layers behind.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mfo.core import Page, Region, RenderArtifact, TranslationUnit, selected_text
from mfo.core.enums import RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.core.parallel import parallel_map
from mfo.storage.atomic import atomic_write_bytes
from mfo.storage.hashing import content_key, sha256_file
from mfo.storage.project import ProjectStore

# The kind tag stored on RenderArtifact.params so masked layers can be told apart from the full
# composited page renders produced once translations are placed onto them.
MASK_KIND = "mask"
RENDER_KIND = "render"


class MaskResult(Protocol):
    """The minimum a masking result must expose to be persisted."""

    @property
    def masked_png(self) -> bytes: ...

    @property
    def mask_png(self) -> bytes: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


MaskPage = Callable[[Path, list[BBox]], MaskResult]


@dataclass(frozen=True)
class _MaskJob:
    page: Page
    original: Path
    boxes: list[BBox]
    page_signature: str
    stale: list[RenderArtifact]


def _regions_fingerprint(regions: list[Region]) -> str:
    """A stable digest of a page's regions, so re-detection invalidates that page's mask."""
    digest = hashlib.sha256()
    for region in regions:
        b = region.bbox
        digest.update(f"{region.id}:{b.x},{b.y},{b.width},{b.height}\n".encode())
    return digest.hexdigest()


def mask_pages(
    store: ProjectStore,
    *,
    mask: MaskPage,
    signature: str,
    force: bool = False,
    jobs: int = 1,
) -> list[RenderArtifact]:
    """Mask the source text on every page, persisting a masked layer + mask. Returns new ones.

    Pages with no regions still get a masked layer (a faithful copy of the original) so the
    downstream render always has a base to typeset onto. Pages are planned and persisted serially
    (deterministic order); only the injected ``mask`` callable runs concurrently when ``jobs > 1``.
    """
    pending: list[_MaskJob] = []
    for page in store.db.list(Page, order_by="idx"):
        regions = store.db.list(Region, where=("page_id", page.id))
        boxes = [region.bbox for region in regions]

        original = store.layout.root / page.image_path
        source_hash = sha256_file(original)
        page_signature = content_key(source_hash, f"{signature}|{_regions_fingerprint(regions)}")

        existing = store.db.list(RenderArtifact, where=("page_id", page.id))
        current = [a for a in existing if a.params.get("kind") == MASK_KIND]
        if not force and any(a.params.get("signature") == page_signature for a in current):
            continue
        pending.append(
            _MaskJob(
                page=page,
                original=original,
                boxes=boxes,
                page_signature=page_signature,
                stale=current,
            )
        )

    results = parallel_map(lambda job: mask(job.original, job.boxes), pending, jobs=jobs)

    created: list[RenderArtifact] = []
    for job, result in zip(pending, results, strict=True):
        # Recompute (forced or stale): drop the prior mask artifact and its files first.
        for artifact in job.stale:
            store.db.delete(RenderArtifact, where=("id", artifact.id))
            (store.layout.root / artifact.output_path).unlink(missing_ok=True)
            prior_mask = artifact.params.get("mask_path")
            if prior_mask:
                (store.layout.root / prior_mask).unlink(missing_ok=True)

        masked_rel = f"renders/{job.page.id}.masked.png"
        mask_rel = f"renders/{job.page.id}.mask.png"
        atomic_write_bytes(store.layout.root / masked_rel, result.masked_png)
        atomic_write_bytes(store.layout.root / mask_rel, result.mask_png)

        artifact = RenderArtifact(
            page_id=job.page.id,
            output_path=masked_rel,
            params={
                "kind": MASK_KIND,
                "signature": job.page_signature,
                "engine": signature,
                "mask_path": mask_rel,
                "regions": len(job.boxes),
                "metadata": dict(result.metadata),
            },
        )
        store.db.save(artifact)
        created.append(artifact)
    return created


# Which named style preset to set each region type in (FR-35). Kept as plain names so storage stays
# free of the render layer; the caller's composite op resolves them. Falls back to "default".
_TYPE_PRESETS: dict[RegionType, str] = {
    RegionType.BUBBLE: "default",
    RegionType.NARRATION: "caption",
    RegionType.CAPTION: "caption",
    RegionType.SIDE_TEXT: "whisper",
    RegionType.SFX: "shout",
    RegionType.UNKNOWN: "default",
}


@dataclass(frozen=True)
class PagePlacement:
    """One translated string to set into ``bbox`` using the named style ``preset`` (FR-34/35)."""

    text: str
    bbox: BBox
    preset: str


class CompositeResult(Protocol):
    """The minimum a compositing result must expose to be persisted."""

    @property
    def render_png(self) -> bytes: ...

    @property
    def overflow(self) -> int: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


CompositePage = Callable[[Path, list[PagePlacement]], CompositeResult]


@dataclass(frozen=True)
class _CompositeJob:
    page: Page
    base_path: Path
    placements: list[PagePlacement]
    page_signature: str
    stale: list[RenderArtifact]


def _unit_sort_key(unit: TranslationUnit, regions: dict[str, Region]) -> tuple[float, str]:
    """Reading-order rank of a unit (from its first placed region), then id, for determinism."""
    for region_id in unit.ordered_region_ids:
        region = regions.get(region_id)
        if region is not None and region.reading_order_index is not None:
            return float(region.reading_order_index), unit.id
    return math.inf, unit.id


def page_placements(store: ProjectStore, page: Page) -> list[PagePlacement]:
    """Build the typesetting placements for a page: one per translated unit, in reading order.

    Each unit's *selected* translation (I-3/FR-29) is placed over the combined box of its regions,
    styled by the unit's leading region type. Units with no selected text or no regions are skipped.
    """
    units = store.db.list(TranslationUnit, where=("page_id", page.id))
    if not units:
        return []
    regions = {region.id: region for region in store.db.list(Region, where=("page_id", page.id))}

    placements: list[PagePlacement] = []
    for unit in sorted(units, key=lambda u: _unit_sort_key(u, regions)):
        text = selected_text(unit)
        if not text:
            continue
        unit_regions = [regions[rid] for rid in unit.ordered_region_ids if rid in regions]
        if not unit_regions:
            continue
        # Skip units whose region was auto-ignored (panel/frame blobs); they aren't real text.
        if all(region.status is RegionStatus.IGNORE for region in unit_regions):
            continue
        preset = _TYPE_PRESETS.get(unit_regions[0].type, "default")
        placements.append(
            PagePlacement(text=text, bbox=BBox.union(r.bbox for r in unit_regions), preset=preset)
        )
    return placements


def _placements_fingerprint(placements: list[PagePlacement]) -> str:
    """A stable digest of a page's placements, so re-translation invalidates its render."""
    digest = hashlib.sha256()
    for placement in placements:
        b = placement.bbox
        digest.update(
            f"{placement.preset}|{b.x},{b.y},{b.width},{b.height}|{placement.text}\n".encode()
        )
    return digest.hexdigest()


def _masked_artifact(store: ProjectStore, page: Page) -> RenderArtifact | None:
    """The current masked-layer artifact for a page, if masking has run."""
    for artifact in store.db.list(RenderArtifact, where=("page_id", page.id)):
        if artifact.params.get("kind") == MASK_KIND:
            return artifact
    return None


def composite_pages(
    store: ProjectStore,
    *,
    composite: CompositePage,
    signature: str,
    force: bool = False,
    jobs: int = 1,
) -> list[RenderArtifact]:
    """Composite each page's translations onto its masked layer, persisting a render. Returns new.

    Typesets the selected translation of every unit onto the page's masked base (falling back to the
    original image if masking hasn't run) and writes ``renders/<page>.render.png`` plus a
    :class:`RenderArtifact` (``kind="render"``) tracing the render to its page (I-2). Compositing is
    injected so storage stays image-free. A per-page signature folds the base layer's signature
    and a fingerprint of the placements, so an unchanged page skips (NFR-8) while a re-mask or
    re-translation correctly invalidates the render; a recompute drops the prior render first.

    Pages are planned and persisted serially (deterministic order); only the injected ``composite``
    callable runs concurrently across pages when ``jobs > 1`` (NFR-5/6/7).
    """
    pending: list[_CompositeJob] = []
    for page in store.db.list(Page, order_by="idx"):
        placements = page_placements(store, page)

        masked = _masked_artifact(store, page)
        if masked is not None:
            base_path = store.layout.root / masked.output_path
            base_signature = str(masked.params.get("signature", masked.id))
        else:
            # No masked layer yet: composite straight onto the (read-only) original page.
            base_path = store.layout.root / page.image_path
            base_signature = sha256_file(base_path)

        page_signature = content_key(
            base_signature, f"{signature}|{_placements_fingerprint(placements)}"
        )

        existing = store.db.list(RenderArtifact, where=("page_id", page.id))
        current = [a for a in existing if a.params.get("kind") == RENDER_KIND]
        if not force and any(a.params.get("signature") == page_signature for a in current):
            continue
        pending.append(
            _CompositeJob(
                page=page,
                base_path=base_path,
                placements=placements,
                page_signature=page_signature,
                stale=current,
            )
        )

    results = parallel_map(lambda job: composite(job.base_path, job.placements), pending, jobs=jobs)

    created: list[RenderArtifact] = []
    for job, result in zip(pending, results, strict=True):
        # Recompute (forced or stale): drop the prior render artifact and its file first.
        for artifact in job.stale:
            store.db.delete(RenderArtifact, where=("id", artifact.id))
            (store.layout.root / artifact.output_path).unlink(missing_ok=True)

        render_rel = f"renders/{job.page.id}.render.png"
        atomic_write_bytes(store.layout.root / render_rel, result.render_png)

        artifact = RenderArtifact(
            page_id=job.page.id,
            output_path=render_rel,
            params={
                "kind": RENDER_KIND,
                "signature": job.page_signature,
                "engine": signature,
                "base_path": job.base_path.relative_to(store.layout.root).as_posix(),
                "placements": len(job.placements),
                "overflow": result.overflow,
                "metadata": dict(result.metadata),
            },
        )
        store.db.save(artifact)
        created.append(artifact)
    return created
