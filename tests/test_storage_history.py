"""Tests for undo/redo page-snapshot history (spec §13; FR-42, I-2/I-3)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import OCRSpan, Page, Project, Region, TranslationCandidate, TranslationUnit
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore, history


def _store(root: Path) -> ProjectStore:
    store = ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))
    store.db.save(
        Page(
            project_id=store.project.id,
            index=0,
            image_path="originals/p0.png",
            width=100,
            height=100,
        )
    )
    return store


def _page(store: ProjectStore, index: int = 0) -> Page:
    return next(p for p in store.db.list(Page) if p.index == index)


def _add_region(store: ProjectStore, page: Page, *, order: int) -> Region:
    region = Region(
        page_id=page.id,
        bbox=BBox(x=0, y=order * 10, width=10, height=8),
        reading_order_index=order,
    )
    store.db.save(region)
    return region


def test_snapshot_restore_round_trips(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _add_region(store, page, order=0)
        store.db.save(OCRSpan(region_id=region.id, text="hi"))
        cand = TranslationCandidate(text="yo")
        store.db.save(
            TranslationUnit(
                page_id=page.id,
                ordered_region_ids=[region.id],
                candidates=[cand],
                selected_candidate_id=cand.id,
            )
        )
        snap = history.snapshot_page(store, page.id)

        store.db.delete(OCRSpan, where=("region_id", region.id))
        store.db.delete(Region, where=("id", region.id))
        store.db.delete(TranslationUnit, where=("page_id", page.id))
        assert store.db.list(Region) == []

        history.restore_page(store, page.id, snap)
        assert len(store.db.list(Region)) == 1
        assert store.db.list(OCRSpan)[0].text == "hi"
        assert store.db.list(TranslationUnit)[0].ordered_region_ids == [region.id]


def test_record_undo_redo_a_region_op(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _add_region(store, page, order=0)
        with history.record(store, page.id, "delete_region"):
            store.db.delete(Region, where=("id", region.id))
        assert store.db.list(Region) == []

        assert history.undo(store) == page.id
        assert len(store.db.list(Region)) == 1  # restored

        assert history.redo(store) == page.id
        assert store.db.list(Region) == []  # re-applied


def test_no_op_record_adds_no_entry(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        _add_region(store, page, order=0)
        with history.record(store, page.id, "noop"):
            pass  # nothing changed
        assert history.history_list(store) == []
        assert history.undo(store) is None


def test_per_page_and_global_scope(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        p1 = _page(store)
        store.db.save(
            Page(
                project_id=store.project.id,
                index=1,
                image_path="originals/p1.png",
                width=100,
                height=100,
            )
        )
        p2 = _page(store, index=1)
        r1 = _add_region(store, p1, order=0)
        r2 = _add_region(store, p2, order=0)
        with history.record(store, p1.id, "e1"):
            store.db.save(r1.model_copy(update={"reading_order_index": 5}))
        with history.record(store, p2.id, "e2"):
            store.db.save(r2.model_copy(update={"reading_order_index": 7}))

        # A per-page undo on p1 restores only p1, leaving p2's edit intact.
        assert history.undo(store, page_id=p1.id) == p1.id
        assert store.db.get(Region, r1.id).reading_order_index == 0  # type: ignore[union-attr]
        assert store.db.get(Region, r2.id).reading_order_index == 7  # type: ignore[union-attr]

        # A global undo then hits the remaining (p2) edit.
        assert history.undo(store) == p2.id
        assert store.db.get(Region, r2.id).reading_order_index == 0  # type: ignore[union-attr]


def test_new_edit_truncates_the_redo_tail(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _add_region(store, page, order=0)
        with history.record(store, page.id, "a"):
            store.db.save(region.model_copy(update={"reading_order_index": 1}))
        history.undo(store)  # roll "a" back

        current = store.db.get(Region, region.id)
        assert current is not None
        with history.record(store, page.id, "b"):
            store.db.save(current.model_copy(update={"reading_order_index": 2}))

        # The undone "a" is no longer redoable; only "b" remains in the log.
        assert history.redo(store) is None
        entries = history.history_list(store)
        assert [e["action"] for e in entries] == ["b"]


def test_page_rev_bumps_on_each_committed_edit(tmp_path: Path) -> None:
    # Optimistic-concurrency revision (SG-8): every committed mutation advances it; a no-op doesn't.
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _add_region(store, page, order=0)
        assert history.page_rev(store, page.id) == 0

        with history.record(store, page.id, "a"):
            store.db.save(region.model_copy(update={"reading_order_index": 1}))
        assert history.page_rev(store, page.id) == 1

        with history.record(store, page.id, "noop"):
            pass  # nothing changed → no history entry and no rev bump
        assert history.page_rev(store, page.id) == 1

        with history.record(store, page.id, "b"):
            current = store.db.get(Region, region.id)
            assert current is not None
            store.db.save(current.model_copy(update={"reading_order_index": 2}))
        assert history.page_rev(store, page.id) == 2


def test_page_rev_advances_on_undo_and_redo(tmp_path: Path) -> None:
    # Undo/redo also change page state, so they must move the revision forward (never revert it).
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _add_region(store, page, order=0)
        with history.record(store, page.id, "a"):
            store.db.save(region.model_copy(update={"reading_order_index": 1}))
        assert history.page_rev(store, page.id) == 1

        history.undo(store, page_id=page.id)
        assert history.page_rev(store, page.id) == 2  # rev is monotonic, not reverted

        history.redo(store, page_id=page.id)
        assert history.page_rev(store, page.id) == 3


def test_page_rev_is_zero_for_unknown_page(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        assert history.page_rev(store, "pg_does_not_exist") == 0


def test_history_survives_reopen(tmp_path: Path) -> None:
    with _store(tmp_path / "proj") as store:
        page = _page(store)
        region = _add_region(store, page, order=0)
        with history.record(store, page.id, "delete_region"):
            store.db.delete(Region, where=("id", region.id))

    with ProjectStore.open(tmp_path / "proj") as reopened:
        assert reopened.db.list(Region) == []
        assert history.undo(reopened) == page.id  # the persisted entry is still undoable
        assert len(reopened.db.list(Region)) == 1
