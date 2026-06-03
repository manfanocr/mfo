"""Tests for the bounded, order-preserving parallel map (NFR-5/6/7; §20)."""

from __future__ import annotations

import threading
import time
from unittest import mock

from mfo.core.parallel import parallel_map, resolve_jobs


def test_preserves_input_order_serially() -> None:
    assert parallel_map(lambda x: x * 2, [1, 2, 3], jobs=1) == [2, 4, 6]


def test_empty_input() -> None:
    assert parallel_map(lambda x: x, [], jobs=4) == []


def test_preserves_input_order_when_parallel() -> None:
    # Items that "finish" out of order (later items sleep less) must still come back in order.
    def work(x: int) -> int:
        time.sleep(0.02 * (5 - x))
        return x * 10

    assert parallel_map(work, [0, 1, 2, 3, 4], jobs=4) == [0, 10, 20, 30, 40]


def test_result_independent_of_worker_count() -> None:
    items = list(range(20))

    def work(x: int) -> int:
        return x * x

    assert parallel_map(work, items, jobs=1) == parallel_map(work, items, jobs=8)


def test_parallel_actually_uses_multiple_threads() -> None:
    seen: set[int] = set()
    barrier = threading.Barrier(3, timeout=5)

    def work(x: int) -> int:
        # If the pool ran these serially this barrier would never trip and raise BrokenBarrier.
        barrier.wait()
        seen.add(threading.get_ident())
        return x

    assert parallel_map(work, [1, 2, 3], jobs=3) == [1, 2, 3]
    assert len(seen) == 3  # three distinct worker threads


def test_single_item_runs_inline_without_a_pool() -> None:
    with mock.patch("mfo.core.parallel.ThreadPoolExecutor") as pool:
        assert parallel_map(lambda x: x + 1, [41], jobs=8) == [42]
        pool.assert_not_called()


def test_resolve_jobs_positive_passthrough() -> None:
    assert resolve_jobs(1) == 1
    assert resolve_jobs(4) == 4


def test_resolve_jobs_auto_is_bounded() -> None:
    with mock.patch("mfo.core.parallel.os.cpu_count", return_value=64):
        assert resolve_jobs(0) == 8  # capped at _MAX_AUTO_JOBS
    with mock.patch("mfo.core.parallel.os.cpu_count", return_value=2):
        assert resolve_jobs(0) == 2
    with mock.patch("mfo.core.parallel.os.cpu_count", return_value=None):
        assert resolve_jobs(-1) == 1
