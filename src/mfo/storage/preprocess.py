"""Cache preprocessing derivatives and persist their metadata onto pages (§10.2; I-5, NFR-7/8).

The image transform is *injected* (the vision layer supplies it) so storage stays free of any
imaging dependency. Each page's derivative is content-addressed by ``hash(source, config)`` and
written to the project cache; the result is recorded on ``Page.preprocessing``. Re-running skips
pages whose source and config are unchanged (NFR-8) and verifies the original is byte-identical
afterwards, enforcing the non-destructive invariant (I-1, FR-3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mfo.core import Page
from mfo.core.parallel import parallel_map
from mfo.storage.hashing import content_key, sha256_file
from mfo.storage.project import ProjectStore

Transform = Callable[[Path], tuple[bytes, dict[str, Any]]]


@dataclass(frozen=True)
class _Job:
    page: Page
    original: Path
    source_hash: str
    cache_key: str


def preprocess_pages(
    store: ProjectStore,
    *,
    transform: Transform,
    signature: str,
    force: bool = False,
    jobs: int = 1,
) -> list[Page]:
    """Preprocess every page, caching derivatives and recording metadata. Returns updated pages.

    Pages are planned and persisted serially (single SQLite connection, deterministic order); only
    the injected ``transform`` runs concurrently across pages when ``jobs > 1`` (NFR-5/6/7).
    """
    pending: list[_Job] = []
    for page in store.db.list(Page, order_by="idx"):
        original = store.layout.root / page.image_path
        source_hash = sha256_file(original)
        cache_key = content_key(source_hash, signature)

        current = page.preprocessing
        if not force and current.get("cache_key") == cache_key and store.cache.has(cache_key):
            continue
        pending.append(
            _Job(page=page, original=original, source_hash=source_hash, cache_key=cache_key)
        )

    results = parallel_map(lambda job: transform(job.original), pending, jobs=jobs)

    updated: list[Page] = []
    for job, (derivative, metadata) in zip(pending, results, strict=True):
        if sha256_file(job.original) != job.source_hash:  # never mutate the source (I-1)
            raise RuntimeError(f"preprocessing modified the source image {job.page.image_path}")
        store.cache.write_bytes(job.cache_key, derivative)
        enriched: dict[str, Any] = {
            **metadata,
            "cache_key": job.cache_key,
            "source_sha256": job.source_hash,
            "signature": signature,
        }
        new_page = job.page.model_copy(update={"preprocessing": enriched})
        store.db.save(new_page)
        updated.append(new_page)
    return updated
