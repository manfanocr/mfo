"""Tests for the series-level shared glossary model (SG-2, SG-3; FR-23/24/25; I-2)."""

from __future__ import annotations

from mfo.core import (
    GlossaryEntry,
    SeriesGlossary,
    merge_entries,
    merge_glossaries,
    remove_entry,
    upsert_entry,
)


def test_upsert_appends_a_new_term() -> None:
    series = SeriesGlossary(name="Saga")
    out = upsert_entry(series, GlossaryEntry(source="太郎", target="Taro"))
    assert [e.source for e in out.entries] == ["太郎"]
    assert out.name == "Saga"  # other fields preserved


def test_upsert_replaces_in_place_preserving_order() -> None:
    series = SeriesGlossary(
        entries=(
            GlossaryEntry(source="太郎", target="Taro"),
            GlossaryEntry(source="鬼", target="ogre"),
        )
    )
    out = upsert_entry(series, GlossaryEntry(source="鬼", target="oni"))
    assert [(e.source, e.target) for e in out.entries] == [("太郎", "Taro"), ("鬼", "oni")]


def test_remove_entry_drops_the_term() -> None:
    series = SeriesGlossary(entries=(GlossaryEntry(source="鬼", target="oni"),))
    assert remove_entry(series, "鬼").entries == ()
    assert remove_entry(series, "absent").entries == series.entries  # no-op


def test_merge_entries_upserts_each_in_order() -> None:
    series = SeriesGlossary(entries=(GlossaryEntry(source="鬼", target="ogre"),))
    incoming = (
        GlossaryEntry(source="鬼", target="oni"),  # replaces
        GlossaryEntry(source="太郎", target="Taro"),  # appends
    )
    out = merge_entries(series, incoming)
    assert [(e.source, e.target) for e in out.entries] == [("鬼", "oni"), ("太郎", "Taro")]


def test_merge_glossaries_project_overrides_series() -> None:
    # The per-volume decision wins over the cross-volume default for the same source term (SG-2).
    project = (GlossaryEntry(source="鬼", target="demon"),)
    series = (
        GlossaryEntry(source="鬼", target="oni"),  # shadowed by the project entry
        GlossaryEntry(source="太郎", target="Taro"),  # inherited
    )
    merged = merge_glossaries(project, series)
    assert [(e.source, e.target) for e in merged] == [("鬼", "demon"), ("太郎", "Taro")]


def test_merge_glossaries_empty_series_is_just_the_project() -> None:
    project = (GlossaryEntry(source="鬼", target="oni"),)
    assert merge_glossaries(project, ()) == project
