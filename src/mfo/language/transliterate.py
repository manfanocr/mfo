"""Transliteration adapters for SFX (sound effects) (spec §10.6; SG-5; FR-14; NFR-17).

Onomatopoeia is usually better *transliterated* (ドーン → "DOON") than translated like dialogue, so
the reader hears the sound. Transliteration is pluggable behind the :class:`Transliterator`
protocol (NFR-17): the offline default :class:`KanaTransliterator` maps Japanese kana to Latin
letters with no dependency or model download (keeping the core path offline, I-7/I-8), and a
third-party romanizer can register via the ``mfo.transliterators`` entry-point group (batch 8.3).

This is pure (no I/O): the storage stage that attaches a transliteration as an SFX candidate on a
unit lives in :mod:`mfo.storage.sfx`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mfo.core.plugins import TRANSLITERATOR_GROUP, resolve_factory


class Transliterator(Protocol):
    """A swappable transliterator (NFR-17). ``name``/``version`` identify it for caching."""

    name: str
    version: str

    def transliterate(self, text: str, *, source_lang: str) -> str: ...


# Kana → romaji. Two-kana digraphs (きゃ → "kya") must be tried before single kana, so they are kept
# in their own table consulted first. Katakana shares the romaji of its hiragana counterpart; rather
# than list both, :meth:`KanaTransliterator.transliterate` folds katakana onto hiragana first.
_DIGRAPHS: dict[str, str] = {
    "きゃ": "kya",
    "きゅ": "kyu",
    "きょ": "kyo",
    "しゃ": "sha",
    "しゅ": "shu",
    "しょ": "sho",
    "ちゃ": "cha",
    "ちゅ": "chu",
    "ちょ": "cho",
    "にゃ": "nya",
    "にゅ": "nyu",
    "にょ": "nyo",
    "ひゃ": "hya",
    "ひゅ": "hyu",
    "ひょ": "hyo",
    "みゃ": "mya",
    "みゅ": "myu",
    "みょ": "myo",
    "りゃ": "rya",
    "りゅ": "ryu",
    "りょ": "ryo",
    "ぎゃ": "gya",
    "ぎゅ": "gyu",
    "ぎょ": "gyo",
    "じゃ": "ja",
    "じゅ": "ju",
    "じょ": "jo",
    "びゃ": "bya",
    "びゅ": "byu",
    "びょ": "byo",
    "ぴゃ": "pya",
    "ぴゅ": "pyu",
    "ぴょ": "pyo",
}

_KANA: dict[str, str] = {
    "あ": "a",
    "い": "i",
    "う": "u",
    "え": "e",
    "お": "o",
    "か": "ka",
    "き": "ki",
    "く": "ku",
    "け": "ke",
    "こ": "ko",
    "さ": "sa",
    "し": "shi",
    "す": "su",
    "せ": "se",
    "そ": "so",
    "た": "ta",
    "ち": "chi",
    "つ": "tsu",
    "て": "te",
    "と": "to",
    "な": "na",
    "に": "ni",
    "ぬ": "nu",
    "ね": "ne",
    "の": "no",
    "は": "ha",
    "ひ": "hi",
    "ふ": "fu",
    "へ": "he",
    "ほ": "ho",
    "ま": "ma",
    "み": "mi",
    "む": "mu",
    "め": "me",
    "も": "mo",
    "や": "ya",
    "ゆ": "yu",
    "よ": "yo",
    "ら": "ra",
    "り": "ri",
    "る": "ru",
    "れ": "re",
    "ろ": "ro",
    "わ": "wa",
    "を": "wo",
    "ん": "n",
    "が": "ga",
    "ぎ": "gi",
    "ぐ": "gu",
    "げ": "ge",
    "ご": "go",
    "ざ": "za",
    "じ": "ji",
    "ず": "zu",
    "ぜ": "ze",
    "ぞ": "zo",
    "だ": "da",
    "ぢ": "ji",
    "づ": "zu",
    "で": "de",
    "ど": "do",
    "ば": "ba",
    "び": "bi",
    "ぶ": "bu",
    "べ": "be",
    "ぼ": "bo",
    "ぱ": "pa",
    "ぴ": "pi",
    "ぷ": "pu",
    "ぺ": "pe",
    "ぽ": "po",
    # Small vowels, kept standalone for stray cases the digraph table misses.
    "ゃ": "ya",
    "ゅ": "yu",
    "ょ": "yo",
    "ぁ": "a",
    "ぃ": "i",
    "ぅ": "u",
    "ぇ": "e",
    "ぉ": "o",
}

# Katakana code points sit 0x60 above their hiragana twins; shifting maps カ → か, ー excepted.
_KATAKANA_SHIFT = 0x30A1 - 0x3041
_PROLONGED = "ー"  # katakana long-vowel mark: lengthens the previous vowel


def _to_hiragana(text: str) -> str:
    """Fold katakana onto hiragana so one romaji table serves both scripts."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:  # katakana block (small ァ … ヶ)
            out.append(chr(code - _KATAKANA_SHIFT))
        else:
            out.append(ch)
    return "".join(out)


class KanaTransliterator:
    """Offline kana→romaji romanizer for SFX. Non-kana characters pass through unchanged.

    Handles digraphs (きゃ → "kya"), the small-tsu geminate (どっ → "dot"), and the katakana
    long-vowel mark (ドーン → "doon"). Output is upper-cased, matching the way SFX is usually set.
    """

    name = "kana"
    version = "1"

    def transliterate(self, text: str, *, source_lang: str) -> str:
        kana = _to_hiragana(text)
        out: list[str] = []
        i = 0
        while i < len(kana):
            ch = kana[i]
            pair = kana[i : i + 2]
            if pair in _DIGRAPHS:
                out.append(_DIGRAPHS[pair])
                i += 2
                continue
            if ch == "っ":  # small tsu doubles the next consonant
                nxt = _KANA.get(kana[i + 1 : i + 2], "")
                if nxt:
                    out.append(nxt[0])
                i += 1
                continue
            if ch == _PROLONGED:  # lengthen the previous vowel (doubles the last romaji vowel)
                if out and out[-1] and out[-1][-1] in "aeiou":
                    out.append(out[-1][-1])
                i += 1
                continue
            out.append(_KANA.get(ch, ch))
            i += 1
        return "".join(out).upper()


def kana_transliterator() -> Transliterator:
    """The offline default transliterator (kana→romaji; no dependencies)."""
    return KanaTransliterator()


_FACTORIES: dict[str, Callable[..., Transliterator]] = {"kana": kana_transliterator}


def get_transliterator(name: str = "kana") -> Transliterator:
    """Resolve a transliterator by name: built-ins first, then ``mfo.transliterators`` plugins."""
    return resolve_factory(name, _FACTORIES, TRANSLITERATOR_GROUP, kind="transliterator")()
