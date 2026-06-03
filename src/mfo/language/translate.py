"""Translation adapters (spec §10.6, §12.5, §14.3; FR-21/22; NFR-2/17/23/24/25; MVP-6).

Translation is pluggable behind the :class:`Translator` protocol (NFR-17, §14.3) so providers swap
without touching the pipeline. The default :class:`ArgosTranslator` wraps
`Argos Translate <https://github.com/argosopentech/argos-translate>`_ — offline neural MT (Tech
decision §19) — so the core path needs no network at run time (NFR-23). It is an **optional**
dependency (``pip install 'mfo[translate]'``) imported lazily, so importing this module never pulls
in the heavy MT stack and the rest of the pipeline keeps working without it (I-7).

:class:`ApiTranslator` is the opt-in cloud/LLM adapter (batch 4.4): it talks to any
OpenAI-compatible chat-completions endpoint and is **never the default** — it is selected only by
explicit config (``--translator api``) and configured entirely from environment variables, so no
endpoint or key is ever written to the project (NFR-24/25). It sends only the unit's *text* and
context bundle, never the source page image, and the offline path is unaffected when it is unused
(I-7/I-8). Network I/O lives behind an injectable transport so the adapter stays unit-testable
offline and adds no hard dependency (stdlib ``urllib``).

Each request carries its source text *and* its context bundle (nearby dialogue + page/chapter
locator; see :func:`mfo.core.context.build_context`). The offline engine translates line-by-line and
ignores most of the bundle, but the protocol passes it through so the API/AI adapters (§12.5) can
use it. The storage layer turns each :class:`TranslationResult` into a ``TranslationCandidate`` on
the unit, kept separate from the OCR source (FR-15).
"""

from __future__ import annotations

import os
from collections.abc import Callable
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
        # Argos dereferences the language without checking it exists, so a missing package surfaces
        # as a cryptic ``'NoneType' ... get_translation`` AttributeError. Detect it up front and
        # raise a clear, actionable error instead (I-7).
        languages = {lang.code: lang for lang in argos.get_installed_languages()}
        from_lang = languages.get(request.source_lang)
        to_lang = languages.get(request.target_lang)
        if from_lang is None or to_lang is None or from_lang.get_translation(to_lang) is None:
            raise TranslatorDependencyError(
                f"no offline Argos language package for {request.source_lang!r} -> "
                f"{request.target_lang!r}; install one, e.g.  "
                f"argospm install translate-{request.source_lang}_{request.target_lang}"
            )
        text = str(argos.translate(request.source, request.source_lang, request.target_lang))
        # Argos emits a single best translation without a score.
        return TranslationResult(text=text, confidence=None)


def argos_translator() -> Translator:
    return ArgosTranslator()


# --- Opt-in cloud/LLM adapter (batch 4.4; NFR-24/25, §14.3) --------------------------------------

#: An injectable HTTP transport: ``(url, payload, headers, timeout) -> parsed JSON``. Defaults to a
#: stdlib-``urllib`` POST; tests inject a fake so the adapter exercises offline.
ApiTransport = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]

_STYLE_GUIDANCE = {
    TranslationStyle.LITERAL: "Translate as literally as possible, staying close to the source"
    " wording even at some cost to fluency.",
    TranslationStyle.BALANCED: "Translate faithfully but naturally, balancing accuracy and"
    " readability.",
    TranslationStyle.NATURAL: "Translate into fluent, idiomatic target-language prose.",
    TranslationStyle.LOCALIZED: "Localize freely so it reads as if originally written for the"
    " target audience, adapting idioms and cultural references.",
}


@dataclass(frozen=True)
class ApiTranslatorConfig:
    """Where and how the API adapter calls its backend (NFR-24/25).

    Populated from environment variables by :func:`api_translator`, never from the project file, so
    no endpoint or secret is persisted. ``api_key`` may be empty at construction; the adapter raises
    :class:`TranslatorDependencyError` only when an actual translation is attempted (mirroring the
    offline adapter's lazy dependency check), so config can be saved before a key is set.
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    timeout: float = 60.0


def _http_post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> dict[str, Any]:
    """Default transport: POST ``payload`` as JSON and return the parsed response (stdlib only)."""
    import json
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — user-configured https endpoint, opt-in
        url,
        data=data,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TranslatorDependencyError(
            f"could not reach the translation API at {url}: {exc}"
        ) from exc
    try:
        parsed: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TranslatorDependencyError(
            f"translation API returned a non-JSON response: {exc}"
        ) from exc
    return parsed


def _build_messages(request: TranslationRequest) -> list[dict[str, str]]:
    """Turn a request + its context bundle into OpenAI-style chat messages (FR-22, FR-24, FR-25)."""
    context = request.context
    system = (
        "You are an expert manga/manhua translator. Translate the user's line from "
        f"{request.source_lang} to {request.target_lang}. "
        f"{_STYLE_GUIDANCE.get(request.style, _STYLE_GUIDANCE[TranslationStyle.BALANCED])} "
        "Return only the translated line, with no quotes, notes, or commentary."
    )

    parts: list[str] = []
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
    parts.append(f"Translate this line:\n{request.source}")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _extract_text(data: dict[str, Any]) -> str:
    """Pull the message content out of an OpenAI-compatible chat-completions response."""
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise TranslatorDependencyError(
            f"unexpected translation API response shape: {data!r}"
        ) from exc


class ApiTranslator:
    """Opt-in cloud/LLM translator over an OpenAI-compatible endpoint (NFR-24/25; never default).

    Sends only the unit's text and context bundle (not the source image, NFR-25). The network call
    goes through an injectable ``transport`` so the adapter is unit-testable offline and pulls in no
    hard dependency. ``version`` folds in the model so switching models re-runs the cache (NFR-8).
    """

    name = "api"

    def __init__(
        self, config: ApiTranslatorConfig, *, transport: ApiTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport or _http_post_json
        self.version = f"1:{config.model}"

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if not request.source.strip():
            # Nothing to translate — stay cheap and make no network call (NFR-24).
            return TranslationResult(text="", confidence=None)
        if not self._config.api_key:
            raise TranslatorDependencyError(
                "no API key for the 'api' translator; set MFO_API_KEY (and optionally "
                "MFO_API_BASE_URL / MFO_API_MODEL) to enable it"
            )
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": _build_messages(request),
            "temperature": 0,
        }
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        data = self._transport(url, payload, headers, self._config.timeout)
        return TranslationResult(text=_extract_text(data), confidence=None)


def api_translator() -> Translator:
    """Build the API adapter from environment variables (nothing is read from the project file)."""
    return ApiTranslator(
        ApiTranslatorConfig(
            base_url=os.environ.get("MFO_API_BASE_URL", ApiTranslatorConfig.base_url),
            model=os.environ.get("MFO_API_MODEL", ApiTranslatorConfig.model),
            api_key=os.environ.get("MFO_API_KEY", ""),
            timeout=float(os.environ.get("MFO_API_TIMEOUT", ApiTranslatorConfig.timeout)),
        )
    )


# --- Opt-in DeepL adapter (NFR-24/25, §14.3) -----------------------------------------------------
#
# DeepL is a dedicated MT provider (not OpenAI-compatible), so it gets its own adapter rather than
# riding the `api` one. Like `api` it is **never the default**, configured entirely from environment
# variables (nothing secret persisted), sends only the unit's text (NFR-25), and goes through the
# same injectable transport so it is unit-testable offline and adds no hard dependency.

#: DeepL needs language codes like ``JA`` / ``EN-US``; map the common ones and upper-case the rest.
_DEEPL_LANG = {"ja": "JA", "zh": "ZH", "en": "EN-US", "ko": "KO", "fr": "FR", "de": "DE"}

#: DeepL free keys end in ``:fx`` and use the ``api-free`` host; the env var can override either.
_DEEPL_FREE_URL = "https://api-free.deepl.com/v2/translate"


@dataclass(frozen=True)
class DeepLTranslatorConfig:
    """Where and how the DeepL adapter calls its backend (NFR-24/25).

    Populated from environment variables by :func:`deepl_translator`, never from the project file.
    ``api_key`` may be empty at construction; the adapter raises only when a translation is
    attempted (mirroring the other adapters' lazy dependency check).
    """

    url: str = _DEEPL_FREE_URL
    api_key: str = ""
    timeout: float = 60.0


def _deepl_lang(code: str) -> str:
    return _DEEPL_LANG.get(code.lower(), code.upper())


class DeepLTranslator:
    """Opt-in translator over DeepL's REST API (NFR-24/25; never default).

    Sends only the unit's text (not its image, NFR-25). The context bundle is accepted but unused —
    DeepL has no slot for surrounding dialogue; the LLM ``api`` adapter is the context-aware path.
    """

    name = "deepl"
    version = "1"

    def __init__(
        self, config: DeepLTranslatorConfig, *, transport: ApiTransport | None = None
    ) -> None:
        self._config = config
        self._transport = transport or _http_post_json

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if not request.source.strip():
            # Nothing to translate — stay cheap and make no network call (NFR-24).
            return TranslationResult(text="", confidence=None)
        if not self._config.api_key:
            raise TranslatorDependencyError(
                "no API key for the 'deepl' translator; set MFO_DEEPL_API_KEY (and optionally "
                "MFO_DEEPL_API_URL for the pro endpoint) to enable it"
            )
        payload: dict[str, Any] = {
            "text": [request.source],
            "source_lang": _deepl_lang(request.source_lang),
            "target_lang": _deepl_lang(request.target_lang),
        }
        headers = {"Authorization": f"DeepL-Auth-Key {self._config.api_key}"}
        data = self._transport(self._config.url, payload, headers, self._config.timeout)
        return TranslationResult(text=_extract_deepl_text(data), confidence=None)


def _extract_deepl_text(data: dict[str, Any]) -> str:
    """Pull the translated text out of a DeepL ``/v2/translate`` response."""
    try:
        return str(data["translations"][0]["text"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise TranslatorDependencyError(f"unexpected DeepL API response shape: {data!r}") from exc


def deepl_translator() -> Translator:
    """Build the DeepL adapter from environment variables (nothing read from the project file)."""
    return DeepLTranslator(
        DeepLTranslatorConfig(
            url=os.environ.get("MFO_DEEPL_API_URL", _DEEPL_FREE_URL),
            api_key=os.environ.get("MFO_DEEPL_API_KEY", ""),
            timeout=float(os.environ.get("MFO_DEEPL_TIMEOUT", DeepLTranslatorConfig.timeout)),
        )
    )


_FACTORIES = {"argos": argos_translator, "api": api_translator, "deepl": deepl_translator}


def get_translator(name: str = "argos") -> Translator:
    """Resolve a translator by config name (NFR-17). Raises ``ValueError`` if unknown."""
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"unknown translator {name!r}; available: {known}") from None
    return factory()
