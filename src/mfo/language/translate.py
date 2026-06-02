"""Translation adapters (spec §10.6, §12.5; FR-21, FR-22; NFR-2, NFR-17, NFR-23; MVP-6).

Translation is pluggable behind the :class:`Translator` protocol so cloud/LLM adapters can be added
later (batch 4.4) without touching the pipeline. The default :class:`ArgosTranslator` wraps
`Argos Translate <https://github.com/argosopentech/argos-translate>`_ — offline neural MT (Tech
decision §19) — so the core path needs no network at run time (NFR-23). It is an **optional**
dependency (``pip install 'mfo[translate]'``) imported lazily, so importing this module never pulls
in the heavy MT stack and the rest of the pipeline keeps working without it (I-7).

Each request carries its source text *and* its context bundle (nearby dialogue + page/chapter
locator; see :func:`mfo.core.context.build_context`). The offline engine translates line-by-line and
ignores most of the bundle, but the protocol passes it through so the AI adapters (M7, §12.5) can
use it. The storage layer turns each :class:`TranslationResult` into a ``TranslationCandidate`` on
the unit, kept separate from the OCR source (FR-15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from mfo.core.enums import TranslationStyle


@dataclass(frozen=True)
class TranslationRequest:
    """One unit to translate, with its language pair, context bundle, and style (FR-22, FR-25)."""

    source: str
    source_lang: str
    target_lang: str
    context: dict[str, Any] = field(default_factory=dict)
    # Requested register (FR-25). The offline engine can't restyle; context-aware adapters (M7) use
    # it. Glossary terms applicable to this unit ride along in ``context["glossary"]`` (FR-24).
    style: TranslationStyle = TranslationStyle.BALANCED


@dataclass(frozen=True)
class TranslationResult:
    """A translation produced for a request, with optional confidence (FR-12 parity)."""

    text: str
    confidence: float | None = None


class Translator(Protocol):
    """A swappable translator (NFR-17). ``name``/``version`` identify it for caching."""

    name: str
    version: str

    def translate(self, request: TranslationRequest) -> TranslationResult: ...


class TranslatorDependencyError(RuntimeError):
    """Raised when a translator's optional dependency or language package is unavailable (I-7)."""


class ArgosTranslator:
    """Offline neural MT via Argos Translate. Language packages load lazily on first use."""

    name = "argos"
    version = "1"  # adapter version; bump if the underlying model identity changes

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if not request.source.strip():
            # Nothing to translate (e.g. a unit with no OCR text yet); keep it cheap and offline.
            return TranslationResult(text="", confidence=None)
        try:
            from argostranslate import translate as argos
        except ImportError as exc:  # optional dependency not installed
            raise TranslatorDependencyError(
                "argostranslate is not installed; install it with:  pip install 'mfo[translate]'"
            ) from exc
        text = str(argos.translate(request.source, request.source_lang, request.target_lang))
        # Argos emits a single best translation without a score.
        return TranslationResult(text=text, confidence=None)


def argos_translator() -> Translator:
    return ArgosTranslator()


_FACTORIES = {"argos": argos_translator}


def get_translator(name: str = "argos") -> Translator:
    """Resolve a translator by config name (NFR-17). Raises ``ValueError`` if unknown."""
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"unknown translator {name!r}; available: {known}") from None
    return factory()
