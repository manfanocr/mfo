"""Tests for resolving a unit's selected translation (FR-26, FR-41/42; I-2/I-3)."""

from __future__ import annotations

from mfo.core import (
    TranslationCandidate,
    TranslationUnit,
    selected_candidate,
    selected_text,
)
from mfo.core.enums import CandidateKind


def _unit_with_two_candidates() -> tuple[
    TranslationUnit, TranslationCandidate, TranslationCandidate
]:
    raw = TranslationCandidate(text="machine", kind=CandidateKind.RAW)
    manual = TranslationCandidate(text="human", kind=CandidateKind.MANUAL)
    unit = TranslationUnit(candidates=[raw, manual], selected_candidate_id=manual.id)
    return unit, raw, manual


def test_selected_candidate_returns_the_chosen_one() -> None:
    unit, _raw, manual = _unit_with_two_candidates()
    assert selected_candidate(unit) is manual
    assert selected_text(unit) == "human"


def test_selected_candidate_none_when_unselected() -> None:
    unit = TranslationUnit(candidates=[TranslationCandidate(text="x")])
    assert selected_candidate(unit) is None
    assert selected_text(unit) == ""


def test_selected_text_empty_for_bare_unit() -> None:
    assert selected_text(TranslationUnit()) == ""
