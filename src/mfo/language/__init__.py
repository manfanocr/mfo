"""Language layer: translation adapters, glossary, context builder, AI assist."""

from __future__ import annotations

from mfo.language.assist import (
    AiAssistant,
    AiAssistantConfig,
    AssistDependencyError,
    AssistRequest,
    AssistSuggestion,
    LlmAssistant,
    ai_assistant,
    get_assistant,
)
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
from mfo.language.transliterate import (
    KanaTransliterator,
    Transliterator,
    get_transliterator,
    kana_transliterator,
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
    # transliterate (SFX, batch 8.7)
    "KanaTransliterator",
    "Transliterator",
    "get_transliterator",
    "kana_transliterator",
    # assist (AI layer, batch 7.1)
    "AiAssistant",
    "AiAssistantConfig",
    "AssistDependencyError",
    "AssistRequest",
    "AssistSuggestion",
    "LlmAssistant",
    "ai_assistant",
    "get_assistant",
]
