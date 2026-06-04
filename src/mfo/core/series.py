"""Series-level shared glossary — cross-volume terminology memory (spec §17; SG-2, SG-3; I-2).

A single manga/manhua series spans many volumes, each its own mfo project. Character names,
honorifics, and pinned terms decided in volume 1 should carry into volume 2 without re-entering
them (SG-2/SG-3). The :class:`SeriesGlossary` is that shared store: a plain, frozen, serializable
container of :class:`~mfo.core.glossary.GlossaryEntry` records that lives **outside** any one
project so several volumes can point at it. It layers *below* the per-project glossary (project
entries win — see :func:`~mfo.core.glossary.merge_glossaries`).

This module is pure (no I/O): the on-disk store and its export/import for team sharing live in
:mod:`mfo.storage.series`.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from mfo.core.glossary import GlossaryEntry


class SeriesGlossary(BaseModel):
    """A named, shared glossary persisted across the volumes of one series (SG-2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = ""  # human label for the series (e.g. the work's title); optional
    entries: tuple[GlossaryEntry, ...] = ()


def upsert_entry(series: SeriesGlossary, entry: GlossaryEntry) -> SeriesGlossary:
    """Add ``entry`` to ``series`` (or replace the existing one with the same source term).

    Order is preserved: an existing term is updated in place; a new term is appended. This is how a
    volume *promotes* a settled term into the shared store (SG-3).
    """
    kept = [existing for existing in series.entries if existing.source != entry.source]
    replaced = any(existing.source == entry.source for existing in series.entries)
    entries = (
        tuple(e if e.source != entry.source else entry for e in series.entries)
        if replaced
        else (*kept, entry)
    )
    return series.model_copy(update={"entries": entries})


def remove_entry(series: SeriesGlossary, source: str) -> SeriesGlossary:
    """Return ``series`` without the entry whose source term is ``source`` (a no-op if absent)."""
    return series.model_copy(
        update={"entries": tuple(e for e in series.entries if e.source != source)}
    )


def merge_entries(series: SeriesGlossary, incoming: Sequence[GlossaryEntry]) -> SeriesGlossary:
    """Fold ``incoming`` entries into ``series`` (upsert each, in order) — used by import (SG-2)."""
    merged = series
    for entry in incoming:
        merged = upsert_entry(merged, entry)
    return merged
