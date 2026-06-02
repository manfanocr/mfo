"""Glossary & terminology consistency for translation (spec §10.6, §12.5; FR-23, FR-24).

A glossary pins how recurring source terms — character names, honorifics, place names, jargon —
should read in the target, so the same term renders the same way everywhere (FR-23). This module is
pure: it has no I/O and no provider dependency. It is used two ways by the translation stage:

* **Injection** (FR-24, §12.5): the entries applicable to a unit's source travel in its context
  bundle, so a context-aware translator (the AI adapters in M7) can honour them.
* **Enforcement** (FR-23): because the offline engine can't be instructed, each entry also carries
  the variant spellings a machine tends to emit (``aliases``); :func:`apply_glossary` rewrites those
  to the canonical ``target`` in the output deterministically, which is how name/term consistency is
  actually guaranteed on the offline path.

Entries are plain, frozen, serializable records persisted in ``Project.config`` (round-tripped via
:func:`entries_from_config` / :func:`entries_to_config`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict


class GlossaryEntry(BaseModel):
    """One pinned term: a source term, its canonical target, and known machine variants."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str  # term as it appears in the source text (used for applicability + injection)
    target: str  # canonical target rendering to enforce
    aliases: tuple[str, ...] = ()  # other target renderings to normalize to ``target``
    notes: str | None = None  # optional human note (e.g. gender, register)


def applicable_entries(source_text: str, entries: Sequence[GlossaryEntry]) -> list[GlossaryEntry]:
    """The glossary entries whose source term occurs in ``source_text`` (order preserved)."""
    return [entry for entry in entries if entry.source and entry.source in source_text]


def glossary_terms(entries: Sequence[GlossaryEntry]) -> list[dict[str, str]]:
    """Render entries as plain ``{source, target}`` dicts for injection into a context bundle."""
    return [{"source": entry.source, "target": entry.target} for entry in entries]


def apply_glossary(text: str, source_text: str, entries: Sequence[GlossaryEntry]) -> str:
    """Enforce the glossary on a translation: normalize known variant spellings to the canonical.

    For every entry applicable to ``source_text``, each alias occurrence in ``text`` is rewritten to
    the entry's ``target`` (longer aliases first, so a longer variant wins over a substring of it).
    This is deterministic and offline; it makes recurring terms render consistently (FR-23).
    """
    for entry in applicable_entries(source_text, entries):
        for alias in sorted(entry.aliases, key=len, reverse=True):
            if alias and alias != entry.target:
                text = text.replace(alias, entry.target)
    return text


def entries_from_config(raw: Any) -> tuple[GlossaryEntry, ...]:
    """Load glossary entries from their persisted ``Project.config`` representation."""
    if not raw:
        return ()
    return tuple(GlossaryEntry(**item) for item in raw)


def entries_to_config(entries: Sequence[GlossaryEntry]) -> list[dict[str, Any]]:
    """Serialize glossary entries to a plain list of dicts for ``Project.config``."""
    return [entry.model_dump() for entry in entries]
