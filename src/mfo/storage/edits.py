"""Append-only edit history for translation units (spec §11 EditRecord; FR-42; I-2/I-3).

Human (and, later, auto-applied) changes are recorded as immutable :class:`EditRecord`s so the
full source → OCR → translation → edit graph stays inspectable (I-2) and every change remains
auditable and reversible (I-3). Records are never updated in place; each change appends a new row.
This is the scaffolding the review editor (M6) and the mapping export build on.
"""

from __future__ import annotations

from mfo.core import EditAction, EditRecord
from mfo.storage.project import ProjectStore


def record_edit(
    store: ProjectStore,
    *,
    unit_id: str,
    before: str,
    after: str,
    action: EditAction,
    editor: str = "user",
) -> EditRecord:
    """Append an edit record for a unit and return it."""
    record = EditRecord(
        translation_unit_id=unit_id,
        before=before,
        after=after,
        action=action,
        editor=editor,
    )
    store.db.save(record)
    return record


def list_edits(store: ProjectStore, unit_id: str | None = None) -> list[EditRecord]:
    """List edit records, optionally for a single unit, oldest change first."""
    where = ("translation_unit_id", unit_id) if unit_id is not None else None
    records = store.db.list(EditRecord, where=where)
    return sorted(records, key=lambda record: (record.timestamp, record.id))
