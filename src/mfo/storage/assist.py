"""Apply the AI assist layer to translated units, in one of three modes (spec §12.4; batch 7.2).

This stage is the *application* half of the optional AI layer: the language adapter
(:mod:`mfo.language.assist`) produces a structured suggestion for a unit, and this module decides
what to persist, per :class:`~mfo.core.enums.AssistMode` (§12.4):

* **assist** — attach the AI candidates only; never touch the selection (suggestions only).
* **review** — also *highlight* the AI candidate as the recommended one.
* **auto** — also *apply* the AI candidate automatically, but only when its confidence clears a
  threshold, and always with an audit record.

Three invariants hold across every mode (FR-29, I-3): the AI never **overwrites** anything — it
appends candidates; it never changes the selection of a **human-approved** unit (one whose selected
candidate is ``MANUAL``); and any selection change it does make is recorded as an
:class:`~mfo.core.models.EditRecord` so the change stays auditable and reversible. The suggestion
callable is *injected* so storage keeps no provider dependency (mirroring the translate stage), and
the AI baseline it refines (``draft``) is the unit's machine/human translation, never a prior AI
output, so re-running is idempotent and cacheable (NFR-8).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Protocol

from mfo.core import EditAction, Page, TranslationCandidate, TranslationUnit
from mfo.core.enums import AssistMode, CandidateKind
from mfo.storage.edits import record_edit
from mfo.storage.hashing import content_key
from mfo.storage.project import ProjectStore

# Candidate kinds owned by the assist stage: replaced wholesale on each run so suggestions never
# accumulate. RAW (the machine draft) and MANUAL (human text) are always preserved.
_AI_KINDS = frozenset({CandidateKind.AI, CandidateKind.LITERAL, CandidateKind.NATURAL})

# Default confidence an AUTO suggestion must reach before it is applied (§12.4).
DEFAULT_MIN_CONFIDENCE = 0.8


class Suggested(Protocol):
    """The structured AI suggestion this stage persists (satisfied by ``AssistSuggestion``).

    Declared as read-only properties so the frozen ``AssistSuggestion`` dataclass matches it.
    """

    @property
    def candidate(self) -> str: ...

    @property
    def literal(self) -> str | None: ...

    @property
    def readability(self) -> str | None: ...

    @property
    def shortened(self) -> str | None: ...

    @property
    def confidence(self) -> float | None: ...

    @property
    def rationale(self) -> str | None: ...

    @property
    def warnings(self) -> list[str]: ...

    @property
    def speaker_shift(self) -> bool | None: ...


#: Injected suggestion callable: ``(source, draft, context) -> Suggested``.
Suggest = Callable[[str, str, dict[str, Any]], Suggested]


def _candidate_text(unit: TranslationUnit, candidate_id: str | None) -> str:
    """Text of the unit's candidate with ``candidate_id`` (empty if none/missing)."""
    if candidate_id is None:
        return ""
    for candidate in unit.candidates:
        if candidate.id == candidate_id:
            return candidate.text
    return ""


def _draft_text(unit: TranslationUnit) -> str:
    """The baseline the AI refines: the human (``MANUAL``) text if any, else the machine (``RAW``).

    Deliberately ignores prior AI candidates so re-running assist feeds the same draft each time
    (idempotent, cacheable — NFR-8) rather than refining its own previous output.
    """
    manual = raw = ""
    for candidate in unit.candidates:
        if candidate.kind is CandidateKind.MANUAL and not manual:
            manual = candidate.text
        elif candidate.kind is CandidateKind.RAW and not raw:
            raw = candidate.text
    return manual or raw


def _is_approved(unit: TranslationUnit) -> bool:
    """Whether the selected candidate is human-entered (``MANUAL``); never auto-override it."""
    if unit.selected_candidate_id is None:
        return False
    selected = next((c for c in unit.candidates if c.id == unit.selected_candidate_id), None)
    return selected is not None and selected.kind is CandidateKind.MANUAL


def _compose_rationale(suggestion: Suggested) -> str | None:
    """Fold the suggestion's rationale, shortened alternative, warnings, and speaker hint into one
    human-readable note kept on the AI candidate (FR-30, I-4)."""
    parts: list[str] = []
    if suggestion.rationale:
        parts.append(suggestion.rationale)
    shortened = (suggestion.shortened or "").strip()
    if shortened and shortened != suggestion.candidate.strip():
        parts.append(f"Tight-bubble alternative: {shortened}")
    if suggestion.warnings:
        parts.append("Warnings: " + "; ".join(suggestion.warnings))
    if suggestion.speaker_shift:
        parts.append("Likely speaker change.")
    return " | ".join(parts) or None


def _build_candidates(suggestion: Suggested) -> list[TranslationCandidate]:
    """Turn a suggestion into candidates: the primary AI one, plus distinct literal/readability."""
    primary_text = suggestion.candidate.strip()
    if not primary_text:
        return []
    out = [
        TranslationCandidate(
            text=suggestion.candidate,
            kind=CandidateKind.AI,
            confidence=suggestion.confidence,
            rationale=_compose_rationale(suggestion),
        )
    ]
    literal = (suggestion.literal or "").strip()
    if literal and literal != primary_text:
        out.append(TranslationCandidate(text=suggestion.literal or "", kind=CandidateKind.LITERAL))
    readability = (suggestion.readability or "").strip()
    if readability and readability != primary_text:
        out.append(
            TranslationCandidate(text=suggestion.readability or "", kind=CandidateKind.NATURAL)
        )
    return out


def _apply_suggestion(
    unit: TranslationUnit,
    suggestion: Suggested,
    *,
    mode: AssistMode,
    min_confidence: float,
) -> TranslationUnit:
    """Attach AI candidates and resolve the selection per ``mode``, preserving approved text."""
    preserved = [c for c in unit.candidates if c.kind not in _AI_KINDS]
    ai_candidates = _build_candidates(suggestion)
    candidates = [*preserved, *ai_candidates]
    primary = ai_candidates[0] if ai_candidates else None

    # Default: keep the human/draft choice (suggestions only). If the old selection pointed at an AI
    # candidate we just dropped, fall back to the draft candidate so nothing dangles.
    preserved_ids = {c.id for c in preserved}
    selected: str | None
    if unit.selected_candidate_id in preserved_ids:
        selected = unit.selected_candidate_id
    else:
        selected = _draft_id(preserved)

    # review/auto promote the AI candidate — but never over human-approved text (FR-29, I-3).
    if primary is not None and not _is_approved(unit):
        promote = mode is AssistMode.REVIEW or (
            mode is AssistMode.AUTO
            and primary.confidence is not None
            and primary.confidence >= min_confidence
        )
        if promote:
            selected = primary.id

    if selected is None and candidates:
        selected = primary.id if primary is not None else candidates[0].id

    return unit.model_copy(update={"candidates": candidates, "selected_candidate_id": selected})


def _draft_id(preserved: list[TranslationCandidate]) -> str | None:
    """Id of the draft candidate (``MANUAL`` first, else ``RAW``, else the last preserved)."""
    for kind in (CandidateKind.MANUAL, CandidateKind.RAW):
        for candidate in preserved:
            if candidate.kind is kind:
                return candidate.id
    return preserved[-1].id if preserved else None


def _units_fingerprint(units: list[TranslationUnit], *, mode: AssistMode) -> str:
    """A stable digest of a page's units for assist, so re-OCR/re-translate/edits invalidate it.

    Folds each unit's source, *draft* (not its current selection, which assist may change),
    context, and approved flag — plus the mode — so a cached page is skipped only when re-running
    would do the same thing (NFR-8).
    """
    digest = hashlib.sha256()
    digest.update(f"mode={mode.value}\n".encode())
    for unit in sorted(units, key=lambda u: u.id):
        digest.update(
            f"{unit.id}:{unit.source_bundle}:{_draft_text(unit)}:{_is_approved(unit)}:".encode()
        )
        digest.update(json.dumps(unit.context_bundle, sort_keys=True, ensure_ascii=False).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def assist_units(
    store: ProjectStore,
    *,
    suggest: Suggest,
    signature: str,
    mode: AssistMode = AssistMode.ASSIST,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    force: bool = False,
) -> list[TranslationUnit]:
    """Run the AI assistant over every page's units in ``mode``; returns those it (re)processed.

    Each unit's machine/human draft and context bundle are sent to ``suggest``; the structured
    result is attached as AI candidate(s) and the selection resolved per ``mode`` (never overriding
    approved text, FR-29). Selection changes are recorded as audit edits (I-3). Unchanged pages are
    skipped unless ``force`` (NFR-8).
    """
    updated: list[TranslationUnit] = []
    for page in store.db.list(Page, order_by="idx"):
        units = store.db.list(TranslationUnit, where=("page_id", page.id))
        if not units:
            continue

        page_signature = content_key(
            f"assist|{signature}|{mode.value}|{min_confidence}",
            _units_fingerprint(units, mode=mode),
        )
        if not force and page.assist.get("signature") == page_signature:
            continue

        new_units: list[TranslationUnit] = []
        applied = 0
        for unit in units:
            source = unit.source_bundle
            draft = _draft_text(unit)
            if not source.strip() and not draft.strip():
                # Nothing to refine — leave the empty unit untouched.
                new_units.append(unit)
                continue
            suggestion = suggest(source, draft, unit.context_bundle)
            new_unit = _apply_suggestion(unit, suggestion, mode=mode, min_confidence=min_confidence)
            new_units.append(new_unit)
            if new_unit.selected_candidate_id != unit.selected_candidate_id:
                applied += 1
                record_edit(
                    store,
                    unit_id=unit.id,
                    before=_candidate_text(unit, unit.selected_candidate_id),
                    after=_candidate_text(new_unit, new_unit.selected_candidate_id),
                    action=EditAction.SELECT_CANDIDATE,
                    editor=f"ai:{mode.value}",
                )

        store.db.save_all(new_units)
        store.db.save(
            page.model_copy(
                update={
                    "assist": {
                        "signature": page_signature,
                        "assistant": signature,
                        "mode": mode.value,
                        "applied": applied,
                        "count": len(new_units),
                    }
                }
            )
        )
        updated.extend(new_units)
    return updated
