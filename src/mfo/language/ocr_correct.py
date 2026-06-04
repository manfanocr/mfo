"""LLM OCR correction adapter (spec §17; SG-7; FR-12, FR-13, FR-30; NFR-17/24/25; I-3, I-7).

An **opt-in** helper, built on the same M7 AI plumbing as :mod:`mfo.language.assist`: given a
recognized line that OCR was unsure about, it proposes corrected *readings* — alternate
transcriptions the recognizer may have got wrong (e.g. a confused kanji). It only ever **suggests**:
the storage stage records the proposals as :attr:`OCRSpan.alternatives`, never overwriting the
recognized text (I-3). It is off the core path (I-7/I-8): there is no offline default, nothing runs
unless explicitly invoked, it is configured entirely from ``MFO_AI_*`` / ``MFO_API_*`` env vars (no
endpoint or key is persisted, NFR-24/25), and it sends only the text line — never the page image
(NFR-25) — over an injectable transport, so it is unit-testable offline and adds no hard dependency.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from mfo.core.plugins import OCR_CORRECTOR_GROUP, resolve_factory
from mfo.language.assist import AiAssistantConfig
from mfo.language.translate import ApiTransport, TranslatorDependencyError, _http_post_json

# How many corrected readings to ask for (and keep) per uncertain line.
DEFAULT_MAX_ALTERNATIVES = 3


@dataclass(frozen=True)
class OcrCorrectionRequest:
    """One uncertain OCR line to correct: its text, language, and how many readings to propose."""

    text: str
    source_lang: str
    max_alternatives: int = DEFAULT_MAX_ALTERNATIVES


@dataclass(frozen=True)
class OcrCorrection:
    """Structured OCR-correction output: alternate readings, plus optional confidence/rationale.

    ``alternatives`` are proposed corrected transcriptions of the *source* line (not translations),
    most-likely first; an empty list means the model offered no correction. Every field is optional
    so a terse or partial model reply degrades gracefully (§12.3 spirit).
    """

    alternatives: list[str] = field(default_factory=list)
    confidence: float | None = None
    rationale: str | None = None


class OcrCorrector(Protocol):
    """A swappable OCR corrector (NFR-17). ``name``/``version`` identify it for caching/audit."""

    name: str
    version: str

    def correct(self, request: OcrCorrectionRequest) -> OcrCorrection: ...


class OcrCorrectorDependencyError(RuntimeError):
    """Raised when the OCR corrector's backend is unavailable or misconfigured (I-7)."""


def _build_messages(request: OcrCorrectionRequest) -> list[dict[str, str]]:
    """Turn a request into OpenAI-style chat messages asking for corrected readings as JSON."""
    system = (
        "You are an expert OCR proofreader for manga/manhua. You are given a single text line that "
        f"an OCR engine read from a {request.source_lang} page and was unsure about. Propose up to "
        f"{request.max_alternatives} corrected readings of the ORIGINAL line (do NOT translate). "
        "Keep the same language and script; fix only likely misrecognitions. Respond with ONLY a "
        "single minified JSON object (no markdown, no code fences, no prose) with these keys: "
        '"alternatives" (array of strings, corrected readings, most likely first; empty if it '
        'already looks correct), "confidence" (number 0-1), "rationale" (string, brief).'
    )
    user = f"OCR line:\n{request.text}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _strip_fences(text: str) -> str:
    """Drop a markdown code fence some models wrap JSON in, despite instructions not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[: -len("```")]
    return stripped.strip()


def _alternatives(value: object, limit: int) -> list[str]:
    """Normalize the alternatives field to a list of distinct non-empty strings (capped at N)."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item not in out:
            out.append(item.strip())
        if len(out) >= limit:
            break
    return out


def _opt_confidence(value: object) -> float | None:
    """Coerce a model-reported confidence to a float clamped to ``[0, 1]`` (I-4), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(max(0.0, min(1.0, float(value))), 3)


def _parse_correction(content: str, *, limit: int) -> OcrCorrection:
    """Parse the model's JSON reply into an :class:`OcrCorrection`, defensively."""
    try:
        data = json.loads(_strip_fences(content))
    except json.JSONDecodeError as exc:
        raise OcrCorrectorDependencyError(
            f"OCR corrector returned non-JSON content: {content!r}"
        ) from exc
    if not isinstance(data, dict):
        raise OcrCorrectorDependencyError(f"OCR corrector returned a non-object JSON: {data!r}")
    rationale = data.get("rationale")
    return OcrCorrection(
        alternatives=_alternatives(data.get("alternatives"), limit),
        confidence=_opt_confidence(data.get("confidence")),
        rationale=rationale.strip() if isinstance(rationale, str) and rationale.strip() else None,
    )


def _extract_content(data: dict[str, Any]) -> str:
    """Pull the message content out of an OpenAI-compatible chat-completions response."""
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise OcrCorrectorDependencyError(
            f"unexpected OCR corrector response shape: {data!r}"
        ) from exc


class LlmOcrCorrector:
    """Opt-in OCR corrector over an OpenAI-compatible endpoint (NFR-24/25; never on the core path).

    Sends only the recognized line (not the page image, NFR-25) through an injectable ``transport``,
    so it is unit-testable offline and pulls in no hard dependency. ``version`` folds in the model
    so switching models re-runs any cache and stays auditable (NFR-8/27).
    """

    name = "llm"

    def __init__(self, config: AiAssistantConfig, *, transport: ApiTransport | None = None) -> None:
        self._config = config
        self._transport = transport or _http_post_json
        self.version = f"1:{config.model}"

    def correct(self, request: OcrCorrectionRequest) -> OcrCorrection:
        if not request.text.strip():
            # Nothing to correct — stay cheap and make no network call (NFR-24).
            return OcrCorrection()
        if not self._config.api_key:
            raise OcrCorrectorDependencyError(
                "no API key for OCR correction; set MFO_AI_API_KEY or MFO_API_KEY (and optionally "
                "MFO_AI_BASE_URL / MFO_AI_MODEL) to enable it"
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
            raise OcrCorrectorDependencyError(str(exc)) from exc
        return _parse_correction(_extract_content(data), limit=request.max_alternatives)


def _env(*names: str, default: str = "") -> str:
    """First set env var among ``names`` (powers the ``MFO_AI_*`` → ``MFO_API_*`` fallback)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def llm_ocr_corrector() -> OcrCorrector:
    """Build the LLM OCR corrector from environment variables (nothing read from the project file).

    Reuses the AI-layer config: ``MFO_AI_*`` wins, otherwise the ``MFO_API_*`` set, so one
    OpenAI-compatible endpoint serves translation, AI review, and OCR correction.
    """
    timeout = _env("MFO_AI_TIMEOUT", "MFO_API_TIMEOUT", default=str(AiAssistantConfig.timeout))
    return LlmOcrCorrector(
        AiAssistantConfig(
            base_url=_env(
                "MFO_AI_BASE_URL", "MFO_API_BASE_URL", default=AiAssistantConfig.base_url
            ),
            model=_env("MFO_AI_MODEL", "MFO_API_MODEL", default=AiAssistantConfig.model),
            api_key=_env("MFO_AI_API_KEY", "MFO_API_KEY"),
            timeout=float(timeout),
        )
    )


_FACTORIES = {"llm": llm_ocr_corrector}


def get_ocr_corrector(name: str = "llm") -> OcrCorrector:
    """Resolve an OCR corrector by config name (NFR-17): built-ins first, then plugins.

    Names resolve from the built-ins, then the ``mfo.ocr_correctors`` entry-point group.
    """
    return resolve_factory(name, _FACTORIES, OCR_CORRECTOR_GROUP, kind="OCR corrector")()
