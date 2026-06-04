"""Tests for the persisted series-preset store (SG-4)."""

from __future__ import annotations

import json
from pathlib import Path

from mfo.core.enums import TranslationStyle
from mfo.core.presets import RenderPreset, SeriesPreset, SeriesPresetStore
from mfo.storage.presets import (
    SERIES_PRESETS_VERSION,
    load_series_presets,
    save_series_presets,
)


def test_missing_store_loads_empty(tmp_path: Path) -> None:
    assert load_series_presets(tmp_path / "nope.json") == SeriesPresetStore()


def test_save_load_round_trips_losslessly(tmp_path: Path) -> None:
    store = SeriesPresetStore(
        presets=(
            SeriesPreset(
                name="house",
                style=TranslationStyle.NATURAL,
                glossary_path="/series/glossary.json",
                render=RenderPreset(pad=3, border=6),
            ),
            SeriesPreset(name="literal", style=TranslationStyle.LITERAL),
        )
    )
    path = tmp_path / "presets.json"
    save_series_presets(path, store)
    assert load_series_presets(path) == store


def test_store_records_version(tmp_path: Path) -> None:
    path = tmp_path / "presets.json"
    save_series_presets(path, SeriesPresetStore(presets=(SeriesPreset(name="a"),)))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["series_presets_version"] == SERIES_PRESETS_VERSION
