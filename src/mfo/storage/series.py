"""Persist and share the series-level glossary (spec §17; SG-2, SG-3; FR-23/24/25; I-1, NFR-10/11).

The :class:`~mfo.core.series.SeriesGlossary` lives in a single JSON file **outside** any project
directory, so every volume of a series can point at the same store and inherit its terms (SG-2).
The on-disk format *is* the portable export format — :func:`save_series_glossary` writes it, both
for the linked store and for a team-shared export, and :func:`load_series_glossary` reads it back,
which is how the round-trip stays lossless. Writes are atomic (temp + rename) so a crash never
leaves a torn store (NFR-10/11); a missing store loads as an empty glossary so a volume can be
linked before the store exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from mfo.core.series import SeriesGlossary
from mfo.storage.atomic import atomic_write_text

# Bumped when the on-disk series-glossary schema changes incompatibly.
SERIES_GLOSSARY_VERSION = 1


def load_series_glossary(path: Path) -> SeriesGlossary:
    """Read the series glossary at ``path``; an absent store loads as an empty glossary."""
    path = Path(path)
    if not path.exists():
        return SeriesGlossary()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return SeriesGlossary(name=raw.get("name", ""), entries=tuple(raw.get("entries", ())))


def save_series_glossary(path: Path, series: SeriesGlossary) -> Path:
    """Atomically write ``series`` to ``path`` in the portable, versioned format; returns it."""
    payload = {
        "series_glossary_version": SERIES_GLOSSARY_VERSION,
        "name": series.name,
        "entries": [entry.model_dump() for entry in series.entries],
    }
    atomic_write_text(Path(path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return Path(path)
