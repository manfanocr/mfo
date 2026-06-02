"""Tests for glossary terminology consistency (spec FR-23, FR-24; §10.6, §12.5)."""

from __future__ import annotations

from mfo.core import (
    GlossaryEntry,
    applicable_entries,
    apply_glossary,
    entries_from_config,
    entries_to_config,
    glossary_terms,
)


def _glossary() -> tuple[GlossaryEntry, ...]:
    return (
        GlossaryEntry(source="鬼", target="oni", aliases=("demon", "ogre")),
        GlossaryEntry(source="太郎", target="Taro", aliases=("Tarou", "Tarō")),
    )


def test_applicable_entries_match_source_term() -> None:
    entries = _glossary()
    applicable = applicable_entries("太郎は強い", entries)
    assert [e.source for e in applicable] == ["太郎"]


def test_apply_glossary_normalizes_aliases_to_canonical() -> None:
    # The machine rendered the name as "Tarou"; the glossary pins "Taro" (FR-23).
    out = apply_glossary("Tarou is strong", "太郎は強い", _glossary())
    assert out == "Taro is strong"


def test_apply_glossary_enforces_consistency_across_variants() -> None:
    # Two units, same source term, different machine renderings → one canonical output (FR-23).
    glossary = _glossary()
    first = apply_glossary("The ogre attacks", "鬼が来た", glossary)
    second = apply_glossary("A demon appears", "鬼が来た", glossary)
    assert first == "The oni attacks"
    assert second == "A oni appears"


def test_apply_glossary_inert_when_term_not_in_source() -> None:
    # No applicable entry → output is untouched even if an alias happens to appear.
    out = apply_glossary("an ogre here", "猫がいる", _glossary())
    assert out == "an ogre here"


def test_apply_glossary_idempotent_on_canonical() -> None:
    glossary = (GlossaryEntry(source="鬼", target="oni", aliases=("oni",)),)
    out = apply_glossary("the oni", "鬼", glossary)
    assert out == "the oni"


def test_longer_alias_wins_over_substring() -> None:
    glossary = (GlossaryEntry(source="x", target="Skyfort", aliases=("sky", "sky fort")),)
    out = apply_glossary("the sky fort", "x", glossary)
    assert out == "the Skyfort"


def test_glossary_terms_renders_injection_payload() -> None:
    terms = glossary_terms(applicable_entries("太郎", _glossary()))
    assert terms == [{"source": "太郎", "target": "Taro"}]


def test_config_round_trip() -> None:
    entries = _glossary()
    restored = entries_from_config(entries_to_config(entries))
    assert restored == entries


def test_entries_from_empty_config() -> None:
    assert entries_from_config(None) == ()
    assert entries_from_config([]) == ()
