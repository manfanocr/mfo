"""Tests for font fitting & text placement (§10.8; FR-34/35, NFR-3/26; SG-6 groundwork)."""

from __future__ import annotations

import pytest
from PIL import ImageFont

from mfo.core.geometry import BBox
from mfo.render import (
    DEFAULT_PRESET,
    PRESETS,
    StylePreset,
    fit_text,
    get_preset,
    load_font,
    preset_names,
    render_layout,
    typeset,
    wrap_text,
)


def _avail(box: BBox, preset: StylePreset) -> tuple[float, float]:
    return box.width - 2 * preset.padding, box.height - 2 * preset.padding


def test_fit_text_uses_a_large_size_in_a_roomy_box() -> None:
    preset = get_preset(DEFAULT_PRESET)
    box = BBox(x=0, y=0, width=400, height=200)
    layout = fit_text("Hi", box, preset)

    # A short string in a big box fits at (or near) the preset's ceiling without overflowing.
    assert not layout.overflow
    assert layout.font_size == preset.max_size
    avail_w, avail_h = _avail(box, preset)
    assert layout.text_width <= avail_w
    assert layout.text_height <= avail_h


def test_fit_text_wraps_to_box_width_without_overflow() -> None:
    preset = get_preset(DEFAULT_PRESET)
    box = BBox(x=0, y=0, width=120, height=300)
    text = "The quick brown fox jumps over the lazy dog again and again"
    layout = fit_text(text, box, preset)

    assert not layout.overflow
    assert len(layout.lines) > 1  # a narrow box forces wrapping (FR-34)
    avail_w, avail_h = _avail(box, preset)
    font = load_font(preset.font_path, layout.font_size)
    for line in layout.lines:
        assert font.getlength(line) + 2 * preset.stroke_width <= avail_w  # no horizontal overflow
    assert layout.text_height <= avail_h  # no vertical overflow


def test_fit_text_flags_overflow_in_a_tiny_box() -> None:
    preset = get_preset(DEFAULT_PRESET)
    box = BBox(x=0, y=0, width=6, height=6)
    layout = fit_text("overflowing text", box, preset)

    assert layout.overflow  # best-effort: cannot fit even at min size (NFR-3, I-4)
    assert layout.font_size == preset.min_size


def test_wrap_text_breaks_an_overlong_word() -> None:
    font = load_font(None, 24)
    # A single word far wider than the box must be hard-broken into pieces that each fit.
    pieces = wrap_text("supercalifragilisticexpialidocious", font, max_width=40)
    assert len(pieces) > 1
    for piece in pieces:
        assert font.getlength(piece) <= 40


def test_render_layout_produces_a_box_sized_tile_with_visible_text() -> None:
    preset = get_preset(DEFAULT_PRESET)
    box = BBox(x=0, y=0, width=200, height=100)
    tile = typeset("Hello", box, preset)

    assert tile.size == (200, 100)  # sized to the box for the compositor to paste
    assert tile.mode == "RGBA"
    drawn = tile.getchannel("A").getbbox()
    assert drawn is not None  # something was actually drawn
    left, top, right, bottom = drawn
    assert left >= 0 and top >= 0 and right <= 200 and bottom <= 100  # stays within the box


def test_default_preset_outline_paints_stroke_pixels() -> None:
    box = BBox(x=0, y=0, width=200, height=100)
    tile = typeset("Hello", box, get_preset("default"))
    pixels = tile.load()
    opaque = [
        pixels[x, y] for x in range(tile.width) for y in range(tile.height) if pixels[x, y][3] > 0
    ]
    # The default preset draws a white outline under black glyphs (FR-35).
    assert any(px[:3] == (0, 0, 0) for px in opaque)
    assert any(px[0] > 200 and px[1] > 200 and px[2] > 200 for px in opaque)


def test_whisper_preset_has_no_outline() -> None:
    box = BBox(x=0, y=0, width=200, height=100)
    tile = typeset("Hello", box, get_preset("whisper"))
    pixels = tile.load()
    # The whisper preset has no stroke, so there should be no white outline pixels.
    assert not any(
        pixels[x, y][3] > 0 and min(pixels[x, y][:3]) > 200
        for x in range(tile.width)
        for y in range(tile.height)
    )


def test_alignment_shifts_text_horizontally() -> None:
    box = BBox(x=0, y=0, width=300, height=80)
    left_preset = StylePreset("l", align="left", stroke_width=0, stroke_fill=(0, 0, 0, 0))
    right_preset = StylePreset("r", align="right", stroke_width=0, stroke_fill=(0, 0, 0, 0))

    left_tile = render_layout(fit_text("hi", box, left_preset))
    right_tile = render_layout(fit_text("hi", box, right_preset))

    left_box = left_tile.getchannel("A").getbbox()
    right_box = right_tile.getchannel("A").getbbox()
    assert left_box is not None and right_box is not None
    assert left_box[0] < right_box[0]  # left-aligned text starts further left


def test_fit_and_render_are_deterministic() -> None:
    preset = get_preset(DEFAULT_PRESET)
    box = BBox(x=0, y=0, width=160, height=120)
    text = "Deterministic output every time"

    first = fit_text(text, box, preset)
    second = fit_text(text, box, preset)
    assert first == second  # same inputs -> identical layout

    assert render_layout(first).tobytes() == render_layout(second).tobytes()  # NFR-26


def test_presets_registry_is_consistent() -> None:
    assert DEFAULT_PRESET in PRESETS
    assert set(preset_names()) == set(PRESETS)
    assert get_preset("shout").max_size > get_preset("whisper").max_size
    with pytest.raises(ValueError):
        get_preset("does-not-exist")


def test_load_font_default_is_cached() -> None:
    a = load_font(None, 20)
    b = load_font(None, 20)
    assert isinstance(a, ImageFont.FreeTypeFont)
    assert a is b  # cached by (path, size)
