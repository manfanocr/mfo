"""Tests for the opt-in LLM OCR-correction adapter (SG-7).

Exercised entirely offline through an injected transport (no network, no optional deps): registry
(NFR-17), opt-in/no-network-unless-used and text-only (NFR-24/25), request shaping, and defensive
parsing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mfo.language import (
    AiAssistantConfig,
    LlmOcrCorrector,
    OcrCorrectionRequest,
    OcrCorrectorDependencyError,
    get_ocr_corrector,
)
from mfo.language.translate import TranslatorDependencyError


def _request(text: str = "ロ本語", max_alternatives: int = 3) -> OcrCorrectionRequest:
    return OcrCorrectionRequest(text=text, source_lang="ja", max_alternatives=max_alternatives)


def _reply(**fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "alternatives": ["日本語", "日本誤"],
        "confidence": 0.7,
        "rationale": "ロ misread for 日",
    }
    payload.update(fields)
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def _failing_transport(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("transport must not be called")


def test_get_ocr_corrector_resolves_llm() -> None:
    assert get_ocr_corrector("llm").name == "llm"


def test_get_ocr_corrector_unknown_lists_available() -> None:
    with pytest.raises(ValueError, match="unknown OCR corrector 'nope'.*llm"):
        get_ocr_corrector("nope")


def test_empty_text_makes_no_call() -> None:
    adapter = LlmOcrCorrector(AiAssistantConfig(api_key="k"), transport=_failing_transport)
    assert adapter.correct(_request(text="   ")).alternatives == []


def test_without_key_raises_before_calling() -> None:
    adapter = LlmOcrCorrector(AiAssistantConfig(api_key=""), transport=_failing_transport)
    with pytest.raises(OcrCorrectorDependencyError, match="MFO_AI_API_KEY or MFO_API_KEY"):
        adapter.correct(_request())


def test_builds_request_and_parses_correction() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        captured.update(url=url, payload=payload, headers=headers)
        return _reply()

    config = AiAssistantConfig(base_url="https://x.test/v1/", model="m1", api_key="secret")
    correction = LlmOcrCorrector(config, transport=transport).correct(_request())

    assert correction.alternatives == ["日本語", "日本誤"]
    assert correction.confidence == 0.7
    assert correction.rationale == "ロ misread for 日"

    # Text-only request (no image, NFR-25): the source line is in the user message; auth header set.
    assert captured["url"] == "https://x.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    user_msg = captured["payload"]["messages"][-1]["content"]
    assert "ロ本語" in user_msg


def test_alternatives_are_capped_and_deduped() -> None:
    adapter = LlmOcrCorrector(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: _reply(alternatives=["a", "a", "b", "c", "d"]),
    )
    assert adapter.correct(_request(max_alternatives=2)).alternatives == ["a", "b"]


def test_non_json_content_raises() -> None:
    adapter = LlmOcrCorrector(
        AiAssistantConfig(api_key="k"),
        transport=lambda *_: {"choices": [{"message": {"content": "sorry"}}]},
    )
    with pytest.raises(OcrCorrectorDependencyError, match="non-JSON"):
        adapter.correct(_request())


def test_transport_error_is_rephrased() -> None:
    def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TranslatorDependencyError("network down")

    adapter = LlmOcrCorrector(AiAssistantConfig(api_key="k"), transport=boom)
    with pytest.raises(OcrCorrectorDependencyError, match="network down"):
        adapter.correct(_request())
