"""Tests for the offline kana→romaji transliterator (SG-5)."""

from __future__ import annotations

import pytest

from mfo.language.transliterate import KanaTransliterator, get_transliterator


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ドン", "DON"),  # katakana, folded onto hiragana then romanized + upper-cased
        ("ドーン", "DOON"),  # the long-vowel mark lengthens the preceding vowel
        ("ばたん", "BATAN"),
        ("どっと", "DOTTO"),  # small-tsu geminates the next consonant
        ("きゃ", "KYA"),  # digraph
        ("しゃきーん", "SHAKIIN"),  # digraph + long vowel
    ],
)
def test_kana_romanization(text: str, expected: str) -> None:
    assert KanaTransliterator().transliterate(text, source_lang="ja") == expected


def test_non_kana_passes_through() -> None:
    # Latin / punctuation that isn't kana is left as-is (then upper-cased).
    assert KanaTransliterator().transliterate("OK!", source_lang="ja") == "OK!"


def test_get_transliterator_resolves_builtin() -> None:
    assert get_transliterator("kana").name == "kana"
