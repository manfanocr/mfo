"""Undo/redo history for the review editor (spec §13; FR-42, I-2/I-3).

Every review mutation — a region operation or a translation edit — is scoped to a single page, so an
undoable change is captured as a *page snapshot* pair: the affected page's regions, OCR spans, and
translation units serialized before and after the change. Undo restores the ``before`` snapshot;
redo restores ``after``. This snapshot model handles every heterogeneous op uniformly (split adds a
region, merge deletes some and moves OCR, a translation edit rewrites a unit's candidates) without
hand-written inverses, and it persists, so history survives reopen.

Entries carry a per-project monotonic ``seq`` for a strict total order and an ``undone`` flag. Undo
rolls back the highest-``seq`` not-yet-undone entry; redo re-applies the lowest-``seq`` undone one.
A new edit truncates the (per-page) redo tail. Because page states are independent, the same stack
serves both a **global** history (ignore ``page_id``) and a **per-page** one (filter by it): a
per-page undo always restores exactly that page. The append-only ``EditRecord`` audit log is
deliberately *not* snapshotted — undo reverts state, but the audit of what happened stays truthful.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mfo.core import HistoryEntry, OCRSpan, Page, Region, TranslationUnit
from mfo.storage.project import ProjectStore


def page_rev(store: ProjectStore, page_id: str) -> int:
    """Current optimistic-concurrency revision of a page (0 if it has no edits or doesn't exist).

    The revision bumps on every committed review mutation and on undo/redo (see :func:`_bump_rev`),
    so two reviewers can detect a stale write: a mutation carrying an out-of-date revision is a
    conflict (SG-8/SG-10). It is stored on the page itself and is never reverted by undo, so it only
    ever moves forward.
    """
    page = store.db.get(Page, page_id)
    return page.review_rev if page is not None else 0


def _bump_rev(store: ProjectStore, page_id: str) -> None:
    """Advance a page's review revision by one (no-op if the page no longer exists)."""
    page = store.db.get(Page, page_id)
    if page is not None:
        store.db.save(page.model_copy(update={"review_rev": page.review_rev + 1}))


def snapshot_page(store: ProjectStore, page_id: str) -> dict[str, Any]:
    """Serialize a page's mutable review state (regions, their OCR spans, units) to plain JSON."""
    regions = store.db.list(Region, where=("page_id", page_id))
    spans = [
        span
        for region in regions
        for span in store.db.list(OCRSpan, where=("region_id", region.id))
    ]
    units = store.db.list(TranslationUnit, where=("page_id", page_id))
    return {
        "regions": [region.model_dump(mode="json") for region in regions],
        "ocr_spans": [span.model_dump(mode="json") for span in spans],
        "units": [unit.model_dump(mode="json") for unit in units],
    }


def restore_page(store: ProjectStore, page_id: str, snapshot: dict[str, Any]) -> None:
    """Replace a page's regions, OCR spans, and units with a previously captured ``snapshot``."""
    for region in store.db.list(Region, where=("page_id", page_id)):
        store.db.delete(OCRSpan, where=("region_id", region.id))
    store.db.delete(Region, where=("page_id", page_id))
    store.db.delete(TranslationUnit, where=("page_id", page_id))
    store.db.save_all(Region.model_validate(row) for row in snapshot.get("regions", []))
    store.db.save_all(OCRSpan.model_validate(row) for row in snapshot.get("ocr_spans", []))
    store.db.save_all(TranslationUnit.model_validate(row) for row in snapshot.get("units", []))


def _next_seq(store: ProjectStore) -> int:
    return max((entry.seq for entry in store.db.list(HistoryEntry)), default=0) + 1


@contextmanager
def record(
    store: ProjectStore, page_id: str, action: str, *, editor: str = "user"
) -> Iterator[None]:
    """Snapshot ``page_id`` around a mutation and append a history entry if anything changed.

    Truncates the page's redo tail first (a new edit makes any previously-undone entries on that
    page unredoable), then records the before/after snapshots as one undoable step.
    """
    before = snapshot_page(store, page_id)
    yield
    after = snapshot_page(store, page_id)
    if before == after:
        return  # a no-op edit leaves no history
    for entry in store.db.list(HistoryEntry, where=("page_id", page_id)):
        if entry.undone:
            store.db.delete(HistoryEntry, where=("id", entry.id))
    store.db.save(
        HistoryEntry(
            page_id=page_id,
            seq=_next_seq(store),
            action=action,
            editor=editor,
            before=before,
            after=after,
        )
    )
    _bump_rev(store, page_id)  # a committed edit advances the page's concurrency revision


def _entries(store: ProjectStore, page_id: str | None) -> list[HistoryEntry]:
    entries = (
        store.db.list(HistoryEntry, where=("page_id", page_id))
        if page_id is not None
        else store.db.list(HistoryEntry)
    )
    return sorted(entries, key=lambda entry: entry.seq)


def undo(store: ProjectStore, *, page_id: str | None = None) -> str | None:
    """Undo the most recent not-yet-undone edit (globally or on ``page_id``); returns the page."""
    pending = [entry for entry in _entries(store, page_id) if not entry.undone]
    if not pending:
        return None
    entry = max(pending, key=lambda e: e.seq)
    restore_page(store, entry.page_id, entry.before)
    store.db.save(entry.model_copy(update={"undone": True}))
    _bump_rev(store, entry.page_id)  # restoring state is itself a change another reviewer must see
    return entry.page_id


def redo(store: ProjectStore, *, page_id: str | None = None) -> str | None:
    """Re-apply the earliest undone edit (globally, or on ``page_id``). Returns its page id."""
    undone = [entry for entry in _entries(store, page_id) if entry.undone]
    if not undone:
        return None
    entry = min(undone, key=lambda e: e.seq)
    restore_page(store, entry.page_id, entry.after)
    store.db.save(entry.model_copy(update={"undone": False}))
    _bump_rev(store, entry.page_id)
    return entry.page_id


def history_list(store: ProjectStore, *, page_id: str | None = None) -> list[dict[str, Any]]:
    """List history entries newest-first (globally or for one page), for the editor's panel."""
    return [
        {
            "id": entry.id,
            "seq": entry.seq,
            "page_id": entry.page_id,
            "action": entry.action,
            "editor": entry.editor,
            "timestamp": entry.timestamp.isoformat(),
            "undone": entry.undone,
        }
        for entry in reversed(_entries(store, page_id))
    ]
