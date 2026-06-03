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
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from mfo.core import Page, Region, RenderArtifact
from mfo.core.geometry import BBox
from mfo.storage.atomic import atomic_write_bytes
from mfo.storage.hashing import content_key, sha256_file
from mfo.storage.project import ProjectStore

# The kind tag stored on RenderArtifact.params so masked layers can be told apart from the full
# page renders later batches will add under the same page.
MASK_KIND = "mask"


class MaskResult(Protocol):
    """The minimum a masking result must expose to be persisted."""

    @property
    def masked_png(self) -> bytes: ...

    @property
    def mask_png(self) -> bytes: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


MaskPage = Callable[[Path, list[BBox]], MaskResult]


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
) -> list[RenderArtifact]:
    """Mask the source text on every page, persisting a masked layer + mask. Returns new ones.

    Pages with no regions still get a masked layer (a faithful copy of the original) so the
    downstream render always has a base to typeset onto.
    """
    created: list[RenderArtifact] = []
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

        # Recompute (forced or stale): drop the prior mask artifact and its files first.
        for artifact in current:
            store.db.delete(RenderArtifact, where=("id", artifact.id))
            (store.layout.root / artifact.output_path).unlink(missing_ok=True)
            prior_mask = artifact.params.get("mask_path")
            if prior_mask:
                (store.layout.root / prior_mask).unlink(missing_ok=True)

        result = mask(original, boxes)
        masked_rel = f"renders/{page.id}.masked.png"
        mask_rel = f"renders/{page.id}.mask.png"
        atomic_write_bytes(store.layout.root / masked_rel, result.masked_png)
        atomic_write_bytes(store.layout.root / mask_rel, result.mask_png)

        artifact = RenderArtifact(
            page_id=page.id,
            output_path=masked_rel,
            params={
                "kind": MASK_KIND,
                "signature": page_signature,
                "engine": signature,
                "mask_path": mask_rel,
                "regions": len(boxes),
                "metadata": dict(result.metadata),
            },
        )
        store.db.save(artifact)
        created.append(artifact)
    return created
