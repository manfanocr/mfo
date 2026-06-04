"""Tests for per-series style presets (SG-4)."""

from __future__ import annotations

from mfo.core.enums import TranslationStyle
from mfo.core.presets import (
    RenderPreset,
    SeriesPreset,
    SeriesPresetStore,
    find_preset,
    remove_preset,
    series_preset_names,
    upsert_preset,
)


def test_preset_defaults() -> None:
    preset = SeriesPreset(name="house")
    assert preset.style is TranslationStyle.BALANCED
    assert preset.glossary_path is None
    assert preset.render == RenderPreset(pad=2, border=4)


def test_upsert_appends_a_new_preset() -> None:
    store = upsert_preset(SeriesPresetStore(), SeriesPreset(name="a"))
    store = upsert_preset(store, SeriesPreset(name="b"))
    assert series_preset_names(store) == ["a", "b"]


def test_upsert_replaces_in_place_by_name() -> None:
    store = upsert_preset(
        SeriesPresetStore(), SeriesPreset(name="a", style=TranslationStyle.LITERAL)
    )
    store = upsert_preset(store, SeriesPreset(name="b"))
    store = upsert_preset(store, SeriesPreset(name="a", style=TranslationStyle.NATURAL))
    # Order is preserved (a stays first) and the entry is updated, not duplicated.
    assert series_preset_names(store) == ["a", "b"]
    assert find_preset(store, "a").style is TranslationStyle.NATURAL


def test_remove_preset() -> None:
    store = upsert_preset(SeriesPresetStore(), SeriesPreset(name="a"))
    store = upsert_preset(store, SeriesPreset(name="b"))
    store = remove_preset(store, "a")
    assert series_preset_names(store) == ["b"]
    # Removing an absent preset is a no-op.
    assert series_preset_names(remove_preset(store, "missing")) == ["b"]


def test_find_preset_returns_none_when_absent() -> None:
    assert find_preset(SeriesPresetStore(), "nope") is None
