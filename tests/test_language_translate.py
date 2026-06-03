"""Tests for the translation adapters: registry, offline default, and opt-in API adapter (4.4).

Covers FR-21 (pluggable adapters), NFR-17 (swappable by name), and NFR-24/25 (the cloud adapter is
opt-in, makes no network call unless used, and sends only text). The API adapter is exercised
entirely offline via an injected transport, so these tests need no network or optional deps.
"""

from __future__ import annotations

from typing import Any

import pytest

from mfo.core.enums import TranslationStyle
from mfo.language import (
    ApiTranslator,
    ApiTranslatorConfig,
    ArgosTranslator,
    TranslationRequest,
    TranslatorDependencyError,
    api_translator,
    get_translator,
)


def _request(source: str = "こんにちは", **context: Any) -> TranslationRequest:
    return TranslationRequest(
        source=source,
        source_lang="ja",
        target_lang="en",
        context=context,
        style=context.pop("style", TranslationStyle.BALANCED),
    )


# --- registry / NFR-17 ---------------------------------------------------------------------------


def test_get_translator_resolves_known_names() -> None:
    assert get_translator("argos").name == "argos"
    assert get_translator("api").name == "api"


def test_get_translator_unknown_lists_available() -> None:
    with pytest.raises(ValueError, match="unknown translator 'nope'.*api, argos"):
        get_translator("nope")


# --- offline default -----------------------------------------------------------------------------


def test_argos_empty_source_is_offline_noop() -> None:
    # An empty unit must not require the optional argostranslate dependency (I-7).
    result = ArgosTranslator().translate(_request(source="   "))
    assert result.text == ""
    assert result.confidence is None


# --- API adapter: no network unless actually used (NFR-24) ----------------------------------------


def _failing_transport(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("transport must not be called")


def test_api_empty_source_makes_no_call() -> None:
    adapter = ApiTranslator(ApiTranslatorConfig(api_key="k"), transport=_failing_transport)
    assert adapter.translate(_request(source="")).text == ""


def test_api_without_key_raises_before_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ApiTranslator(ApiTranslatorConfig(api_key=""), transport=_failing_transport)
    with pytest.raises(TranslatorDependencyError, match="MFO_API_KEY"):
        adapter.translate(_request())


# --- API adapter: request shaping & response parsing ---------------------------------------------


def test_api_builds_request_and_parses_response() -> None:
    captured: dict[str, Any] = {}

    def transport(
        url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> dict[str, Any]:
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {"choices": [{"message": {"content": "  Hello there  "}}]}

    config = ApiTranslatorConfig(base_url="https://x.test/v1/", model="m1", api_key="secret")
    adapter = ApiTranslator(config, transport=transport)
    result = adapter.translate(
        _request(
            style=TranslationStyle.NATURAL,
            glossary=[{"source": "犬", "target": "Inu"}],
            preceding=["earlier line"],
            following=["later line"],
        )
    )

    assert result.text == "Hello there"  # trimmed
    assert captured["url"] == "https://x.test/v1/chat/completions"  # single slash join
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "m1"
    messages = captured["payload"]["messages"]
    system, user = messages[0]["content"], messages[1]["content"]
    assert "ja to en" in system and "fluent" in system  # NATURAL style guidance
    assert "犬 = Inu" in user  # glossary pinned (FR-24)
    assert "earlier line" in user and "later line" in user  # context bundle (FR-22)
    assert "こんにちは" in user  # the line itself


def test_api_malformed_response_raises() -> None:
    adapter = ApiTranslator(ApiTranslatorConfig(api_key="k"), transport=lambda *_: {"oops": True})
    with pytest.raises(TranslatorDependencyError, match="unexpected translation API response"):
        adapter.translate(_request())


def test_api_version_folds_model_for_cache_keys() -> None:
    # Switching models must change the adapter id so cached pages re-translate (NFR-8).
    a = ApiTranslator(ApiTranslatorConfig(model="m1", api_key="k"))
    b = ApiTranslator(ApiTranslatorConfig(model="m2", api_key="k"))
    assert a.version != b.version
    assert a.version == "1:m1"


# --- env-driven construction; nothing secret is persisted (NFR-25) -------------------------------


def test_api_translator_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MFO_API_KEY", "envkey")
    monkeypatch.setenv("MFO_API_BASE_URL", "https://local.test/v1")
    monkeypatch.setenv("MFO_API_MODEL", "local-model")
    adapter = api_translator()
    assert isinstance(adapter, ApiTranslator)
    assert adapter.version == "1:local-model"


def test_api_translator_without_env_has_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MFO_API_KEY", raising=False)
    # Constructing the adapter must not raise even with no key; the error is deferred to translate.
    adapter = api_translator()
    assert isinstance(adapter, ApiTranslator)


# --- default transport glue (offline, via a fake urlopen) ----------------------------------------


def test_default_transport_posts_json_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import urllib.request

    seen: dict[str, Any] = {}

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def fake_urlopen(request: Any, timeout: float | None = None) -> _Resp:
        seen.update(
            url=request.full_url,
            method=request.get_method(),
            body=request.data,
            content_type=request.headers.get("Content-type"),
        )
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    adapter = ApiTranslator(ApiTranslatorConfig(api_key="k"))  # default transport
    result = adapter.translate(_request())

    assert result.text == "ok"
    assert seen["method"] == "POST"
    assert seen["content_type"] == "application/json"
    assert json.loads(seen["body"])["temperature"] == 0


def test_default_transport_wraps_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    adapter = ApiTranslator(ApiTranslatorConfig(api_key="k"))
    with pytest.raises(TranslatorDependencyError, match="could not reach the translation API"):
        adapter.translate(_request())
