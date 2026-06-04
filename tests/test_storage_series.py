"""Tests for persisting and sharing the series glossary (SG-2, SG-3; I-1, NFR-10/11)."""

from __future__ import annotations

from pathlib import Path

from mfo.core import GlossaryEntry, SeriesGlossary
from mfo.storage import load_series_glossary, save_series_glossary


def test_missing_store_loads_as_empty(tmp_path: Path) -> None:
    series = load_series_glossary(tmp_path / "nope.json")
    assert series == SeriesGlossary()


def test_save_load_round_trips_losslessly(tmp_path: Path) -> None:
    # The on-disk format is the portable export format, so import(export(x)) == x (SG-2).
    series = SeriesGlossary(
        name="Clevatess",
        entries=(
            GlossaryEntry(source="太郎", target="Taro", aliases=("Tarou", "Tarō"), notes="lead"),
            GlossaryEntry(source="鬼", target="oni"),
        ),
    )
    path = save_series_glossary(tmp_path / "series.json", series)
    assert load_series_glossary(path) == series


def test_export_to_a_second_file_matches(tmp_path: Path) -> None:
    series = SeriesGlossary(entries=(GlossaryEntry(source="鬼", target="oni"),))
    linked = save_series_glossary(tmp_path / "linked.json", series)
    # "Export" is just a save to another path; re-import is byte-equivalent in content.
    exported = save_series_glossary(tmp_path / "share.json", load_series_glossary(linked))
    assert load_series_glossary(exported) == series


def test_saved_file_carries_a_version(tmp_path: Path) -> None:
    import json

    path = save_series_glossary(tmp_path / "series.json", SeriesGlossary())
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["series_glossary_version"] == 1
