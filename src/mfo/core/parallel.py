"""Bounded, order-preserving parallel map for the heavy pipeline stages (NFR-5/6/7; §20).

The expensive stages (preprocess, detect, OCR, translate, render) do their work through a *pure*
injected callable that reads an image and returns data — the surrounding storage code only reads
inputs from and writes outputs to the project. That shape lets us run the callable for several
pages concurrently while keeping every database/file write serial and ordered, so the result is
independent of the worker count (determinism, I-5).

Threads — not processes — are the right tool: the callables spend their time in native code
(OpenCV/NumPy, PyTorch, CTranslate2, PaddlePaddle, Pillow) or blocked on network I/O, all of
which release the GIL, and threads share the single SQLite connection's process without pickling
the store or the closures. ``jobs <= 1`` runs inline with no executor, keeping the default path
simple and trivially deterministic for tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Upper bound on auto-resolved workers, so ``--jobs 0`` on a many-core box doesn't spawn an
# unreasonable number of heavy model threads that would thrash memory.
_MAX_AUTO_JOBS = 8


def resolve_jobs(jobs: int) -> int:
    """Resolve a user-supplied ``--jobs`` to a concrete worker count (``>= 1``).

    ``0`` (or any non-positive value) means "auto": the CPU count, capped at :data:`_MAX_AUTO_JOBS`.
    """
    if jobs >= 1:
        return jobs
    return max(1, min(os.cpu_count() or 1, _MAX_AUTO_JOBS))


def parallel_map(func: Callable[[T], R], items: Iterable[T], *, jobs: int) -> list[R]:
    """Apply ``func`` to each item, returning results in input order.

    With ``jobs <= 1`` (or a single item) the work runs inline on the calling thread — no executor,
    no reordering. With more workers the items are processed concurrently on a thread pool while the
    returned list still mirrors the input order, so callers can persist results deterministically.
    """
    materialized = list(items)
    if jobs <= 1 or len(materialized) <= 1:
        return [func(item) for item in materialized]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(func, materialized))
