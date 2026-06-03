"""Tests for the AI-assist application stage and its three modes (§12.4; FR-29; I-3; NFR-8).

The suggestion callable is faked so these tests run entirely offline. They pin the mode behaviours
(assist suggests only, review highlights, auto applies above a threshold), the never-overwrite-
approved invariant (FR-29), audit recording (I-3), and cache skip/idempotency (NFR-8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mfo.core import (
    EditRecord,
    Page,
    Project,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.enums import AssistMode, CandidateKind, EditAction
from mfo.storage import ProjectStore, assist_units


@dataclass(frozen=True)
class _Sugg:
    """A stand-in for ``AssistSuggestion`` satisfying the stage's ``Suggested`` protocol."""

    candidate: str
    literal: str | None = None
    readability: str | None = None
    shortened: str | None = None
    confidence: float | None = None
    rationale: str | None = None
    warnings: list[str] = field(default_factory=list)
    speaker_shift: bool | None = None


def _store(root: Path) -> ProjectStore:
    return ProjectStore.create(root, Project(name="vol", source_lang="ja", target_lang="en"))


def _page(store: ProjectStore, index: int = 0) -> Page:
    page = Page(
        project_id=store.project.id,
        index=index,
        image_path=f"originals/p{index}.png",
        width=200,
        height=400,
    )
    store.db.save(page)
    return page


def _unit(
    store: ProjectStore,
    page: Page,
    *,
    source: str = "こんにちは",
    draft: str = "Hi",
    manual: str | None = None,
) -> TranslationUnit:
    """A post-translation unit: a RAW machine draft, optionally a selected MANUAL (approved) one."""
    raw = TranslationCandidate(text=draft, kind=CandidateKind.RAW, confidence=0.5)
    candidates = [raw]
    selected = raw.id
    if manual is not None:
        man = TranslationCandidate(text=manual, kind=CandidateKind.MANUAL)
        candidates.append(man)
        selected = man.id
    unit = TranslationUnit(
        page_id=page.id,
        source_bundle=source,
        context_bundle={},
        candidates=candidates,
        selected_candidate_id=selected,
    )
    store.db.save(unit)
    return unit


def _selected(unit: TranslationUnit) -> TranslationCandidate:
    return next(c for c in unit.candidates if c.id == unit.selected_candidate_id)


def _suggest(sugg: _Sugg):  # type: ignore[no-untyped-def]
    def _call(source: str, draft: str, context: dict[str, object]) -> _Sugg:
        return sugg

    return _call


# --- mode behaviours (§12.4) ---------------------------------------------------------------------


def test_assist_mode_attaches_candidates_without_changing_selection(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        unit = _unit(store, page)
        before = unit.selected_candidate_id

        out = assist_units(
            store,
            suggest=_suggest(_Sugg(candidate="Hello there", confidence=0.95)),
            signature="fake@1",
            mode=AssistMode.ASSIST,
        )

        saved = out[0]
        assert any(c.kind is CandidateKind.AI and c.text == "Hello there" for c in saved.candidates)
        assert saved.selected_candidate_id == before  # suggestions only — selection untouched
        assert store.db.list(EditRecord) == []  # no selection change → no audit edit


def test_review_mode_highlights_the_ai_candidate(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        _unit(store, page)

        out = assist_units(
            store,
            suggest=_suggest(_Sugg(candidate="Hello there", confidence=0.1)),
            signature="fake@1",
            mode=AssistMode.REVIEW,
        )

        selected = _selected(out[0])
        assert selected.kind is CandidateKind.AI  # promoted regardless of confidence
        assert selected.text == "Hello there"


def test_auto_mode_applies_only_above_threshold(tmp_path: Path) -> None:
    with _store(tmp_path / "hi") as hi:
        page = _page(hi)
        _unit(hi, page)
        out = assist_units(
            hi,
            suggest=_suggest(_Sugg(candidate="Hello there", confidence=0.9)),
            signature="fake@1",
            mode=AssistMode.AUTO,
            min_confidence=0.8,
        )
        assert _selected(out[0]).kind is CandidateKind.AI  # confident → applied

    with _store(tmp_path / "lo") as lo:
        page = _page(lo)
        unit = _unit(lo, page)
        before = unit.selected_candidate_id
        out = assist_units(
            lo,
            suggest=_suggest(_Sugg(candidate="Hello there", confidence=0.5)),
            signature="fake@1",
            mode=AssistMode.AUTO,
            min_confidence=0.8,
        )
        # below threshold → behaves like assist: candidate attached, selection unchanged
        assert any(c.kind is CandidateKind.AI for c in out[0].candidates)
        assert out[0].selected_candidate_id == before


def test_auto_without_confidence_is_not_applied(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        unit = _unit(store, page)
        before = unit.selected_candidate_id
        out = assist_units(
            store,
            suggest=_suggest(_Sugg(candidate="Hello there", confidence=None)),
            signature="fake@1",
            mode=AssistMode.AUTO,
        )
        assert out[0].selected_candidate_id == before  # no confidence → never auto-applied


# --- FR-29 / I-3: never overwrite approved text, and audit what we do change ----------------------


def test_review_does_not_override_approved_manual_text(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        unit = _unit(store, page, manual="My final words")
        approved = unit.selected_candidate_id

        out = assist_units(
            store,
            suggest=_suggest(_Sugg(candidate="AI rewrite", confidence=1.0)),
            signature="fake@1",
            mode=AssistMode.REVIEW,
        )

        saved = out[0]
        # AI candidate still attached (non-destructive), but the human selection is preserved.
        assert any(c.kind is CandidateKind.AI for c in saved.candidates)
        assert saved.selected_candidate_id == approved
        assert _selected(saved).text == "My final words"
        assert store.db.list(EditRecord) == []


def test_auto_application_records_an_audit_edit(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        unit = _unit(store, page, draft="Hi")
        assist_units(
            store,
            suggest=_suggest(_Sugg(candidate="Hello there", confidence=0.99)),
            signature="fake@1",
            mode=AssistMode.AUTO,
        )

        records = store.db.list(EditRecord)
        assert len(records) == 1
        rec = records[0]
        assert rec.translation_unit_id == unit.id
        assert rec.action is EditAction.SELECT_CANDIDATE
        assert rec.editor == "ai:auto"
        assert rec.before == "Hi"
        assert rec.after == "Hello there"


# --- structured output → candidates (§12.3, FR-30) -----------------------------------------------


def test_literal_and_readability_become_distinct_candidates(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        _unit(store, page)
        out = assist_units(
            store,
            suggest=_suggest(
                _Sugg(
                    candidate="Hello there",
                    literal="Hello",
                    readability="Hey!",
                    confidence=0.7,
                )
            ),
            signature="fake@1",
            mode=AssistMode.ASSIST,
        )
        kinds = {c.kind for c in out[0].candidates}
        assert {CandidateKind.AI, CandidateKind.LITERAL, CandidateKind.NATURAL} <= kinds


def test_rationale_folds_warnings_shortened_and_speaker_shift(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        _unit(store, page)
        out = assist_units(
            store,
            suggest=_suggest(
                _Sugg(
                    candidate="Hello there",
                    shortened="Hi!",
                    rationale="casual greeting",
                    warnings=["ambiguous subject"],
                    speaker_shift=True,
                    confidence=0.7,
                )
            ),
            signature="fake@1",
            mode=AssistMode.ASSIST,
        )
        ai = next(c for c in out[0].candidates if c.kind is CandidateKind.AI)
        assert ai.rationale is not None
        assert "casual greeting" in ai.rationale
        assert "Hi!" in ai.rationale  # tight-bubble alternative surfaced (FR-28)
        assert "ambiguous subject" in ai.rationale  # warnings surfaced (I-4)
        assert "speaker change" in ai.rationale.lower()


# --- caching / idempotency (NFR-8) ---------------------------------------------------------------


def test_rerun_is_cached_and_force_reruns(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        _unit(store, page)
        sugg = _suggest(_Sugg(candidate="Hello there", confidence=0.9))

        first = assist_units(store, suggest=sugg, signature="fake@1", mode=AssistMode.REVIEW)
        assert len(first) == 1

        second = assist_units(store, suggest=sugg, signature="fake@1", mode=AssistMode.REVIEW)
        assert second == []  # unchanged page skipped

        forced = assist_units(
            store, suggest=sugg, signature="fake@1", mode=AssistMode.REVIEW, force=True
        )
        assert len(forced) == 1


def test_rerun_replaces_prior_ai_candidates_without_accumulating(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        _unit(store, page)
        assist_units(
            store,
            suggest=_suggest(_Sugg(candidate="First", literal="lit", confidence=0.9)),
            signature="fake@1",
            mode=AssistMode.REVIEW,
        )
        out = assist_units(
            store,
            suggest=_suggest(_Sugg(candidate="Second", confidence=0.9)),
            signature="fake@1",
            mode=AssistMode.REVIEW,
            force=True,
        )
        ai_texts = [c.text for c in out[0].candidates if c.kind in {CandidateKind.AI}]
        assert ai_texts == ["Second"]  # the stale "First" AI candidate is gone
        assert not any(c.kind is CandidateKind.LITERAL for c in out[0].candidates)
        # the RAW machine draft is always preserved as the refinable baseline
        assert any(c.kind is CandidateKind.RAW for c in out[0].candidates)


def test_empty_unit_is_left_untouched(tmp_path: Path) -> None:
    with _store(tmp_path / "p") as store:
        page = _page(store)
        unit = TranslationUnit(page_id=page.id, source_bundle="", context_bundle={})
        store.db.save(unit)

        def _boom(source: str, draft: str, context: dict[str, object]) -> _Sugg:
            raise AssertionError("must not call the assistant for an empty unit")

        out = assist_units(store, suggest=_boom, signature="fake@1", mode=AssistMode.AUTO)
        assert out[0].candidates == []
