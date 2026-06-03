"""Language layer: translation adapters, glossary, context builder, AI assist."""

from __future__ import annotations

from mfo.language.translate import (
    ApiTranslator,
    ApiTranslatorConfig,
    ApiTransport,
    ArgosTranslator,
    DeepLTranslator,
    DeepLTranslatorConfig,
    TranslationRequest,
    TranslationResult,
    Translator,
    TranslatorDependencyError,
    api_translator,
    argos_translator,
    deepl_translator,
    get_translator,
)

__all__ = [
    # translate
    "ApiTranslator",
    "ApiTranslatorConfig",
    "ApiTransport",
    "ArgosTranslator",
    "DeepLTranslator",
    "DeepLTranslatorConfig",
    "Translator",
    "TranslatorDependencyError",
    "TranslationRequest",
    "TranslationResult",
    "api_translator",
    "argos_translator",
    "deepl_translator",
    "get_translator",
]
