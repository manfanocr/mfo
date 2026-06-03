"""Language layer: translation adapters, glossary, context builder, AI assist."""

from __future__ import annotations

from mfo.language.translate import (
    ApiTranslator,
    ApiTranslatorConfig,
    ApiTransport,
    ArgosTranslator,
    TranslationRequest,
    TranslationResult,
    Translator,
    TranslatorDependencyError,
    api_translator,
    argos_translator,
    get_translator,
)

__all__ = [
    # translate
    "ApiTranslator",
    "ApiTranslatorConfig",
    "ApiTransport",
    "ArgosTranslator",
    "Translator",
    "TranslatorDependencyError",
    "TranslationRequest",
    "TranslationResult",
    "api_translator",
    "argos_translator",
    "get_translator",
]
