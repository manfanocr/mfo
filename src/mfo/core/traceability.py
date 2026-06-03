"""Resolve the selected translation of a unit (spec FR-26, FR-41/42; I-2/I-3).

Pure, I/O-free helpers over a :class:`~mfo.core.models.TranslationUnit`: which candidate the unit
currently points at, and the text that would be rendered for it. The mapping export and the
render stage both consume the *selected* translation, so this keeps that resolution in one place.
"""

from __future__ import annotations

from mfo.core.models import TranslationCandidate, TranslationUnit


def selected_candidate(unit: TranslationUnit) -> TranslationCandidate | None:
    """The unit's chosen translation candidate, or ``None`` if none is selected."""
    if unit.selected_candidate_id is None:
        return None
    for candidate in unit.candidates:
        if candidate.id == unit.selected_candidate_id:
            return candidate
    return None


def selected_text(unit: TranslationUnit) -> str:
    """The text of the unit's selected translation; empty string when there is none."""
    candidate = selected_candidate(unit)
    return candidate.text if candidate is not None else ""
