"""Tests for the AI assist adapter (batch 7.1).

Covers FR-27/28/30 (structured suggestions: candidate/literal/readability/shortened/confidence/
rationale/warnings/speaker-shift), NFR-17 (swappable by name), and NFR-24/25 (opt-in, no network
unless used, text-only). The adapter is exercised entirely offline through an injected transport, so
these tests need no network or optional deps. §12.2 — uncertainty is surfaced, not hidden.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mfo.core.enums import TranslationStyle
from mfo.language import (
    AiAssistantConfig,
    AssistDependencyError,
    AssistRequest,
    LlmAssistant,
    ai_assistant,
    get_assistant,
)
from mfo.language.translate import TranslatorDependencyError


def _request(source: str = "こんにちは", draft: str = "Hi", **context: Any) -> AssistRequest:
    return AssistRequest(
        source=source,
        source_lang="ja",
        target_lang="en",
        draft=draft,
        style=context.pop("style", TranslationStyle.BALANCED),
        max_chars=context.pop("max_chars", None),
        context=context,
    )


def _reply(**fields: Any) -> dict[str, Any]:
    payload = {
        "candidate": "Hello there",
        "literal": "Hello",
        "readability": "Hey there!",
        "shortened": "Hi!",
        "confidence": 0.82,
        "rationale": "casual greeting",
        "warnings": ["ambiguous subject"],
        "speaker_shift": True,
    }
    payload.update(fields)
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


# --- registry / NFR-17 ---------------------------------------------------------------------------


def test_get_assistant_resolves_llm() -> None:
    assert get_assistant("llm").name == "llm"


def test_get_assistant_unknown_lists_available() -> None:
    with pytest.raises(ValueError, match="unknown AI assistant 'nope'.*llm"):
        get_assistant("nope")


# --- opt-in: no network unless actually used (NFR-24) --------------------------------------------


def _failing_transport(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("transport must not be called")


def test_empty_source_and_draft_make_no_call() -> None:
    adapter = LlmAssistant(AiAssistantConfig(api_key="k"), transport=_failing_transport)
    result = adapter.suggest(_request(source="   ", draft="  "))
    assert result.candidate == ""


def test_without_key_raises_before_calling() -> None:
    adapter = LlmAssistant(AiAssistantConfig(api_key=""), transport=_failing_transport)
    with pytest.raises(AssistDependencyError, match="MFO_AI_API_KEY or MFO_API_KEY"):
        adapter.suggest(_request())


# --- request shaping & structured parsing (§12.3) ------------------------------------------------


def test_builds_request_and_parses_structured_suggestion() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        captured.update(url=url, payload=payload, headers=headers)
        return _reply()

    config = AiAssistantConfig(base_url="https://x.test/v1/", model="m1", api_key="secret")
    adapter = LlmAssistant(config, transport=transport)
    suggestion = adapter.suggest(
        _request(
            style=TranslationStyle.NATURAL,
            max_chars=12,
            glossary=[{"source": "犬", "target": "Inu"}],
            preceding=["earlier line"],
            following=["later line"],
        )
    )

    # response → structured suggestion
    assert suggestion.candidate == "Hello there"
    assert suggestion.literal == "Hello"
    assert suggestion.readability == "Hey there!"
    assert suggestion.shortened == "Hi!"
    assert suggestion.confidence == 0.82
    assert suggestion.rationale == "casual greeting"
    assert suggestion.warnings == ["ambiguous subject"]
    assert suggestion.speaker_shift is True

    # request shaping
    assert captured["url"] == "https://x.test/v1/chat/completions"  # single-slash join
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "m1"
    messages = captured["payload"]["messages"]
    system, user = messages[0]["content"], messages[1]["content"]
    assert "JSON" in system and "speaker_shift" in system  # asks for structured output
    assert "犬 = Inu" in user  # glossary pinned (FR-24)
    assert "earlier line" in user and "later line" in user  # context (FR-22, §12.5)
    assert "12 characters" in user  # bubble-fit budget (FR-28)
    assert "こんにちは" in user and "Hi" in user  # source + current draft


# --- defensive parsing ---------------------------------------------------------------------------


def test_confidence_is_clamped_and_warnings_normalized() -> None:
    adapter = LlmAssistant(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: _reply(confidence=5, warnings=["ok", "", 3, "  spaced  "]),
    )
    suggestion = adapter.suggest(_request())
    assert suggestion.confidence == 1.0  # clamped to [0, 1]
    assert suggestion.warnings == ["ok", "spaced"]  # blanks/non-strings dropped, trimmed


def test_missing_optional_fields_degrade_to_none() -> None:
    adapter = LlmAssistant(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: {"choices": [{"message": {"content": '{"candidate": "Hi"}'}}]},
    )
    suggestion = adapter.suggest(_request())
    assert suggestion.candidate == "Hi"
    assert suggestion.literal is None
    assert suggestion.confidence is None
    assert suggestion.warnings == []
    assert suggestion.speaker_shift is None


def test_candidate_falls_back_to_readability() -> None:
    adapter = LlmAssistant(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: {
            "choices": [{"message": {"content": '{"readability": "Hey there"}'}}]
        },
    )
    assert adapter.suggest(_request()).candidate == "Hey there"


def test_json_wrapped_in_code_fences_is_parsed() -> None:
    fenced = "```json\n" + json.dumps({"candidate": "Hello"}) + "\n```"
    adapter = LlmAssistant(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: {"choices": [{"message": {"content": fenced}}]},
    )
    assert adapter.suggest(_request()).candidate == "Hello"


def test_non_json_content_raises() -> None:
    adapter = LlmAssistant(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: {"choices": [{"message": {"content": "sorry, I cannot"}}]},
    )
    with pytest.raises(AssistDependencyError, match="non-JSON"):
        adapter.suggest(_request())


def test_response_without_candidate_raises() -> None:
    adapter = LlmAssistant(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: {"choices": [{"message": {"content": '{"rationale": "hmm"}'}}]},
    )
    with pytest.raises(AssistDependencyError, match="no candidate text"):
        adapter.suggest(_request())


def test_malformed_envelope_raises() -> None:
    adapter = LlmAssistant(AiAssistantConfig(api_key="k"), transport=lambda *_: {"oops": True})
    with pytest.raises(AssistDependencyError, match="unexpected AI assistant response"):
        adapter.suggest(_request())


def test_transport_error_is_rephrased_for_ai_layer() -> None:
    def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TranslatorDependencyError("could not reach the translation API")

    adapter = LlmAssistant(AiAssistantConfig(api_key="k"), transport=boom)
    with pytest.raises(AssistDependencyError, match="could not reach"):
        adapter.suggest(_request())


# --- env configuration (NFR-24/25) ---------------------------------------------------------------


def test_ai_assistant_prefers_ai_env_then_falls_back_to_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "MFO_AI_BASE_URL",
        "MFO_AI_MODEL",
        "MFO_AI_API_KEY",
        "MFO_AI_TIMEOUT",
        "MFO_API_BASE_URL",
        "MFO_API_MODEL",
        "MFO_API_KEY",
        "MFO_API_TIMEOUT",
    ):
        monkeypatch.delenv(var, raising=False)
    # MFO_AI_* wins where set; otherwise MFO_API_* is reused (one endpoint for both).
    monkeypatch.setenv("MFO_AI_MODEL", "smart-model")
    monkeypatch.setenv("MFO_API_BASE_URL", "https://shared.test/v1")
    monkeypatch.setenv("MFO_API_KEY", "envkey")
    adapter = ai_assistant()
    assert isinstance(adapter, LlmAssistant)
    assert adapter.version == "1:smart-model"  # AI model override
    # Reuses the shared endpoint/key without a network call (NFR-24): shape the payload to check.
    captured: dict[str, Any] = {}

    def transport(url: str, payload: dict[str, Any], *_: Any) -> dict[str, Any]:
        captured["url"] = url
        return _reply()

    adapter._transport = transport  # type: ignore[attr-defined]
    adapter.suggest(_request())
    assert captured["url"] == "https://shared.test/v1/chat/completions"


def test_ai_assistant_without_env_has_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("MFO_AI_API_KEY", "MFO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AssistDependencyError):
        ai_assistant().suggest(_request())
