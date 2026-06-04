"""Persist the per-series style presets (spec §17; SG-4; FR-25, FR-35; I-1, NFR-10/11).

The :class:`~mfo.core.presets.SeriesPresetStore` lives in a single JSON file **outside** any project
directory, so every volume of a series can point at the same store and adopt its named presets. The
on-disk format *is* the portable export: :func:`save_series_presets` writes it and
:func:`load_series_presets` reads it back, so the round-trip stays lossless. Writes are atomic
(temp + rename) so a crash never leaves a torn store (NFR-10/11); a missing store loads as empty, so
a preset can be created before the file exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from mfo.core.presets import SeriesPreset, SeriesPresetStore
from mfo.storage.atomic import atomic_write_text

# Bumped when the on-disk series-preset schema changes incompatibly.
SERIES_PRESETS_VERSION = 1


def load_series_presets(path: Path) -> SeriesPresetStore:
    """Read the series-preset store at ``path``; an absent store loads as an empty store."""
    path = Path(path)
    if not path.exists():
        return SeriesPresetStore()
    raw = json.loads(path.read_text(encoding="utf-8"))
    presets = tuple(SeriesPreset(**item) for item in raw.get("presets", ()))
    return SeriesPresetStore(presets=presets)


def save_series_presets(path: Path, store: SeriesPresetStore) -> Path:
    """Atomically write ``store`` to ``path`` in the portable, versioned format; returns it."""
    payload = {
        "series_presets_version": SERIES_PRESETS_VERSION,
        "presets": [preset.model_dump() for preset in store.presets],
    }
    atomic_write_text(Path(path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return Path(path)
