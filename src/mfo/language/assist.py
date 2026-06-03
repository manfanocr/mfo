"""AI assist adapter (spec §12.1/12.3/12.5; FR-27, FR-28, FR-30; NFR-17/24/25; batch 7.1).

The AI layer is a **helper**, not the foundation of correctness (spec §12): it takes an already
recognized line (and, optionally, a machine-translated draft) plus the unit's context bundle and
proposes a *structured* set of suggestions — a refined candidate, an optional literal rendering, a
readability-focused rewrite, a bubble-fit shortened alternative, a confidence estimate, a rationale,
free-form warnings, and a likely-speaker-shift hint (§12.3, FR-28).

Like the cloud translator adapters it is **opt-in and never on the core path** (I-7/I-8): there is
no offline default assistant, nothing here runs unless explicitly invoked, and it is configured
entirely from environment variables so no endpoint or key is ever written to the project
(NFR-24/25). It sends only the unit's text and context (never the source page image, NFR-25) over an
injectable transport, so the adapter is unit-testable offline and adds no hard dependency (stdlib
``urllib``, shared with :mod:`mfo.language.translate`).

This batch (7.1) ships the adapter and its structured output only. Wiring AI *modes* (assist /
review / auto) into the pipeline is batch 7.2, and surfacing AI confidence/warnings in the review
queue is batch 7.3.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from mfo.core.enums import TranslationStyle
from mfo.core.plugins import ASSISTANT_GROUP, resolve_factory
from mfo.language.translate import (
    _STYLE_GUIDANCE,
    ApiTransport,
    TranslatorDependencyError,
    _http_post_json,
)


@dataclass(frozen=True)
class AssistRequest:
    """One unit to refine: its source line, an optional draft translation, and its context.

    ``draft`` is the current machine translation to improve (FR-27); it may be empty, in which
    case the assistant translates from ``source`` directly. ``max_chars`` is an optional bubble-fit
    budget that drives the ``shortened`` suggestion (FR-28); ``None`` means no hard limit.
    """

    source: str
    source_lang: str
    target_lang: str
    draft: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    style: TranslationStyle = TranslationStyle.BALANCED
    max_chars: int | None = None


@dataclass(frozen=True)
class AssistSuggestion:
    """Structured AI output for one unit (spec §12.3).

    Every field except ``candidate`` is optional so a partial or terse model response degrades
    gracefully rather than failing. ``confidence`` is clamped to ``[0, 1]`` (I-4); ``warnings``
    surfaces uncertainty rather than hiding it (§12.2); ``speaker_shift`` flags a likely change of
    speaker relative to the preceding line (§12.1).
    """

    candidate: str
    literal: str | None = None
    readability: str | None = None
    shortened: str | None = None
    confidence: float | None = None
    rationale: str | None = None
    warnings: list[str] = field(default_factory=list)
    speaker_shift: bool | None = None


class AiAssistant(Protocol):
    """A swappable AI assistant (NFR-17). ``name``/``version`` identify it for caching/audit."""

    name: str
    version: str

    def suggest(self, request: AssistRequest) -> AssistSuggestion: ...


class AssistDependencyError(RuntimeError):
    """Raised when the AI assistant's backend is unavailable or misconfigured (I-7)."""


@dataclass(frozen=True)
class AiAssistantConfig:
    """Where and how the LLM assistant calls its backend (NFR-24/25).

    Populated from environment variables by :func:`ai_assistant`, never from the project file, so
    no endpoint or secret is persisted. The ``MFO_AI_*`` variables fall back to the ``MFO_API_*``
    set used by the :class:`~mfo.language.translate.ApiTranslator`, so a single OpenAI-compatible
    endpoint serves both — while still allowing a more capable model for AI review via
    ``MFO_AI_MODEL``. ``api_key`` may be empty at construction; the adapter raises only when a
    suggestion is attempted.
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    timeout: float = 60.0


def _build_messages(request: AssistRequest) -> list[dict[str, str]]:
    """Turn a request + context bundle into OpenAI-style chat messages asking for JSON (§12.3)."""
    style = _STYLE_GUIDANCE.get(request.style, _STYLE_GUIDANCE[TranslationStyle.BALANCED])
    system = (
        "You are an expert manga/manhua translation editor. You refine a draft translation for "
        f"readability and bubble fit while staying faithful to the source. {style} "
        "Respond with ONLY a single minified JSON object (no markdown, no code fences, no prose) "
        "with these keys: "
        '"candidate" (string, your best translation), '
        '"literal" (string, a close literal rendering), '
        '"readability" (string, the most natural fluent rewrite), '
        '"shortened" (string, a shorter alternative that fits a tight bubble), '
        '"confidence" (number 0-1), '
        '"rationale" (string, a brief explanation), '
        '"warnings" (array of strings for any uncertainty or ambiguity), '
        '"speaker_shift" (boolean, true if this line likely starts a new speaker).'
    )

    context = request.context
    parts: list[str] = [
        f"Translate from {request.source_lang} to {request.target_lang}.",
    ]
    glossary = context.get("glossary") or []
    if glossary:
        pinned = "; ".join(f"{term['source']} = {term['target']}" for term in glossary)
        parts.append(f"Use these fixed term translations exactly: {pinned}.")
    preceding = context.get("preceding") or []
    following = context.get("following") or []
    if preceding:
        parts.append("Preceding dialogue: " + " / ".join(preceding))
    if following:
        parts.append("Following dialogue: " + " / ".join(following))
    if request.max_chars is not None:
        parts.append(
            f"The bubble fits about {request.max_chars} characters; keep 'shortened' under it."
        )
    parts.append(f"Source line:\n{request.source}")
    if request.draft.strip():
        parts.append(f"Current draft translation:\n{request.draft}")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _strip_fences(text: str) -> str:
    """Drop a markdown code fence some models wrap JSON in, despite instructions not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line (``` or ```json) and a trailing fence if present.
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -len("```")]
    return stripped.strip()


def _opt_str(value: object) -> str | None:
    """A non-empty stripped string, or ``None`` (treats missing/blank/non-string uniformly)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _opt_confidence(value: object) -> float | None:
    """Coerce a model-reported confidence to a float clamped to ``[0, 1]`` (I-4), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(max(0.0, min(1.0, float(value))), 3)


def _warnings(value: object) -> list[str]:
    """Normalize the warnings field to a list of non-empty strings (§12.2 — keep uncertainty)."""
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _parse_suggestion(content: str) -> AssistSuggestion:
    """Parse the model's JSON reply into an :class:`AssistSuggestion`, defensively (§12.3)."""
    try:
        data = json.loads(_strip_fences(content))
    except json.JSONDecodeError as exc:
        raise AssistDependencyError(f"AI assistant returned non-JSON content: {content!r}") from exc
    if not isinstance(data, dict):
        raise AssistDependencyError(f"AI assistant returned a non-object JSON value: {data!r}")
    # The primary candidate is required; fall back to the readability rewrite if the model only
    # filled that in, and only fail if there is no usable translation at all.
    candidate = _opt_str(data.get("candidate")) or _opt_str(data.get("readability"))
    if candidate is None:
        raise AssistDependencyError(f"AI assistant response had no candidate text: {data!r}")
    speaker = data.get("speaker_shift")
    return AssistSuggestion(
        candidate=candidate,
        literal=_opt_str(data.get("literal")),
        readability=_opt_str(data.get("readability")),
        shortened=_opt_str(data.get("shortened")),
        confidence=_opt_confidence(data.get("confidence")),
        rationale=_opt_str(data.get("rationale")),
        warnings=_warnings(data.get("warnings")),
        speaker_shift=speaker if isinstance(speaker, bool) else None,
    )


def _extract_content(data: dict[str, Any]) -> str:
    """Pull the message content out of an OpenAI-compatible chat-completions response."""
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise AssistDependencyError(f"unexpected AI assistant response shape: {data!r}") from exc


class LlmAssistant:
    """Opt-in AI assistant over an OpenAI-compatible endpoint (NFR-24/25; never on the core path).

    Sends only the unit's text and context (not the source image, NFR-25) through an injectable
    ``transport`` so it is unit-testable offline and pulls in no hard dependency. ``version`` folds
    in the model so switching models re-runs any cache and stays auditable (NFR-8/27).
    """

    name = "llm"

    def __init__(self, config: AiAssistantConfig, *, transport: ApiTransport | None = None) -> None:
        self._config = config
        self._transport = transport or _http_post_json
        self.version = f"1:{config.model}"

    def suggest(self, request: AssistRequest) -> AssistSuggestion:
        if not request.source.strip() and not request.draft.strip():
            # Nothing to work with — stay cheap and make no network call (NFR-24).
            return AssistSuggestion(candidate="")
        if not self._config.api_key:
            raise AssistDependencyError(
                "no API key for the AI assistant; set MFO_AI_API_KEY or MFO_API_KEY (and "
                "optionally MFO_AI_BASE_URL / MFO_AI_MODEL) to enable it"
            )
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": _build_messages(request),
            "temperature": 0,
        }
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        try:
            data = self._transport(url, payload, headers, self._config.timeout)
        except TranslatorDependencyError as exc:
            # The shared transport reports transport/JSON failures as a translator error; rephrase
            # it for the AI layer so callers get a consistent exception type.
            raise AssistDependencyError(str(exc)) from exc
        return _parse_suggestion(_extract_content(data))


def _env(*names: str, default: str = "") -> str:
    """First set env var among ``names`` (powers the ``MFO_AI_*`` → ``MFO_API_*`` fallback)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def ai_assistant() -> AiAssistant:
    """Build the LLM assistant from environment variables (nothing is read from the project file).

    ``MFO_AI_*`` wins; otherwise the ``MFO_API_*`` set is reused so one endpoint serves translation
    and AI review.
    """
    timeout = _env("MFO_AI_TIMEOUT", "MFO_API_TIMEOUT", default=str(AiAssistantConfig.timeout))
    return LlmAssistant(
        AiAssistantConfig(
            base_url=_env(
                "MFO_AI_BASE_URL", "MFO_API_BASE_URL", default=AiAssistantConfig.base_url
            ),
            model=_env("MFO_AI_MODEL", "MFO_API_MODEL", default=AiAssistantConfig.model),
            api_key=_env("MFO_AI_API_KEY", "MFO_API_KEY"),
            timeout=float(timeout),
        )
    )


_FACTORIES = {"llm": ai_assistant}


def get_assistant(name: str = "llm") -> AiAssistant:
    """Resolve an AI assistant by config name (NFR-17). Raises ``ValueError`` if unknown.

    Names resolve from the built-ins first, then ``mfo.assistants`` entry-point plugins.
    """
    return resolve_factory(name, _FACTORIES, ASSISTANT_GROUP, kind="AI assistant")()
