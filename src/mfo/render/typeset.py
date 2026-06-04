"""Font fitting & text placement for the render stage (§10.8; FR-34/35, NFR-3; SG-6).

Pure and storage-free. Given a translated string and the bounding box it must live in, this picks
the largest font size at which the text — wrapped to the box width — still fits the box height, then
lays the lines out with the requested alignment and stroke/outline. The result is a
:class:`TextLayout` (what to draw and where) and a :func:`render_layout` that paints it onto a
transparent RGBA tile the size of the box, ready for the compositing batch to paste onto a page.

When the region carries a bubble outline, :func:`fit_text` instead follows the bubble *shape*
(SG-6): it wraps each line to the polygon's interior width at that line's height (via
:mod:`mfo.render.shape`), so text stays inside round/irregular bubbles. A region with no polygon
uses the box path unchanged, so its render is byte-identical.

Design choices, tied to the spec:

* **Readability-first, best-effort fit** (NFR-2/3): the search shrinks the font until the text fits;
  if even the smallest configured size overflows we still emit that size and flag ``overflow``
  rather than fail, so a too-small bubble degrades gracefully instead of breaking the pipeline.
* **Style presets** (FR-35): named :class:`StylePreset`s bundle font, size range, alignment,
  padding, fill and stroke. The offline default needs no font download (Pillow's built-in font),
  keeping the core path offline; a preset may name a ``font_path`` to use any TrueType font instead.
* **Adapters, not hard-coded providers** (NFR-17): font loading goes through an injectable
  :data:`FontLoader`; the default :func:`load_font` is the offline provider.
* **Deterministic** (NFR-26): the fit search and rendering are pure functions of their inputs, so
  the same text + box + preset always yield byte-identical output.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import cast

from PIL import Image, ImageDraw, ImageFont

from mfo.core.geometry import BBox, Point
from mfo.render.shape import band_inner

# An RGBA colour. Text and stroke colours carry their own alpha so a preset can be fully transparent
# (e.g. "no outline") without a separate flag.
RGBA = tuple[int, int, int, int]

_BLACK: RGBA = (0, 0, 0, 255)
_WHITE: RGBA = (255, 255, 255, 255)
_NONE: RGBA = (0, 0, 0, 0)

# A throwaway canvas used only to measure text; measuring needs an ImageDraw but not a real image.
_MEASURE = ImageDraw.Draw(Image.new("L", (1, 1)))


@dataclass(frozen=True)
class StylePreset:
    """A reusable typesetting style: font, size range, alignment, padding, stroke/outline (FR-35).

    ``font_path`` of ``None`` selects Pillow's built-in font so the core path stays offline.
    ``min_size``/``max_size`` bound the fit search. ``line_spacing`` multiplies the natural line
    height. ``padding`` is the inner margin kept clear inside the box on every side.
    """

    name: str
    font_path: str | None = None
    min_size: int = 10
    max_size: int = 48
    line_spacing: float = 1.05
    align: str = "center"
    padding: int = 4
    fill: RGBA = _BLACK
    stroke_width: int = 2
    stroke_fill: RGBA = _WHITE

    def signature(self) -> str:
        """A stable string identifying this style, for content-addressed caching (NFR-7/8)."""
        return (
            f"style@1;name={self.name};font={self.font_path or 'default'};"
            f"size={self.min_size}-{self.max_size};spacing={self.line_spacing};"
            f"align={self.align};pad={self.padding};fill={self.fill};"
            f"stroke={self.stroke_width};strokefill={self.stroke_fill}"
        )


# Built-in presets (FR-35). All use the offline default font so they need no model/font download.
PRESETS: dict[str, StylePreset] = {
    # Ordinary speech: black text with a thin white outline, centred in the bubble.
    "default": StylePreset("default"),
    # Emphatic/shout: bigger and a heavier outline so it reads against busy art.
    "shout": StylePreset("shout", min_size=14, max_size=72, line_spacing=1.0, stroke_width=3),
    # Quiet/aside: smaller, dark-grey, no outline.
    "whisper": StylePreset(
        "whisper",
        min_size=8,
        max_size=28,
        line_spacing=1.1,
        fill=(60, 60, 60, 255),
        stroke_width=0,
        stroke_fill=_NONE,
    ),
    # Narration/caption: left-aligned, no outline, roomier padding.
    "caption": StylePreset(
        "caption",
        min_size=9,
        max_size=32,
        line_spacing=1.15,
        align="left",
        padding=6,
        stroke_width=0,
        stroke_fill=_NONE,
    ),
}

DEFAULT_PRESET = "default"


def preset_names() -> list[str]:
    """The names of the built-in style presets, sorted."""
    return sorted(PRESETS)


def get_preset(name: str) -> StylePreset:
    """Look up a built-in :class:`StylePreset` by name, or raise ``ValueError``."""
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(
            f"Unknown style preset {name!r}. Available: {', '.join(preset_names())}."
        ) from None


@lru_cache(maxsize=256)
def load_font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont:
    """The default offline font provider: a TrueType font, or Pillow's built-in default (NFR-17).

    Cached by ``(font_path, size)`` so a fit search that probes many sizes stays cheap and
    deterministic.
    """
    if font_path:
        return ImageFont.truetype(font_path, size)
    # With an explicit size, Pillow's default is a sizable TrueType face (not the legacy bitmap).
    return cast(ImageFont.FreeTypeFont, ImageFont.load_default(size=size))


# A font provider: maps ``(font_path, size)`` to a font. Injectable so callers can supply their own
# (bundled fonts, a different rasterizer) without this module knowing about it.
FontLoader = Callable[[str | None, int], ImageFont.FreeTypeFont]


@dataclass(frozen=True)
class TextLayout:
    """The result of fitting text to a box: the wrapped lines and the geometry to draw them.

    ``text_width``/``text_height`` are the measured extent of the laid-out block (stroke included);
    ``overflow`` is set when the text could not fit even at the preset's smallest size (NFR-3, I-4).
    """

    text: str
    box: BBox
    preset: StylePreset
    lines: tuple[str, ...]
    font_size: int
    line_height: int
    line_step: int
    text_width: int
    text_height: int
    overflow: bool
    # Bubble-shape-aware layout (SG-6): for a polygon fit, the per-line interior span ``(left,
    # right)`` in box-local coords and the block's top y. ``None`` for an ordinary rectangular box
    # fit, which leaves the existing box-based rendering byte-identical.
    line_bands: tuple[tuple[float, float], ...] | None = None
    y_start: float | None = None


def _line_width(text: str, font: ImageFont.FreeTypeFont, stroke_width: int) -> float:
    """Pixel width of a single line, including the stroke that bleeds out on both sides."""
    return _MEASURE.textlength(text, font=font) + 2 * stroke_width


def _line_height(font: ImageFont.FreeTypeFont, stroke_width: int) -> int:
    """Natural line height (ascent + descent) plus the stroke above and below."""
    ascent, descent = font.getmetrics()
    return ascent + descent + 2 * stroke_width


def _break_word(
    word: str, font: ImageFont.FreeTypeFont, max_width: float, stroke: int
) -> list[str]:
    """Hard-break a single word too wide for the box into pieces that each fit (last-resort)."""
    pieces: list[str] = []
    current = ""
    for char in word:
        if current and _line_width(current + char, font, stroke) > max_width:
            pieces.append(current)
            current = char
        else:
            current += char
    if current:
        pieces.append(current)
    return pieces or [word]


def wrap_text(
    text: str, font: ImageFont.FreeTypeFont, max_width: float, stroke_width: int = 0
) -> list[str]:
    """Greedily wrap ``text`` to ``max_width``, hard-breaking any word that alone overflows."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if _line_width(candidate, font, stroke_width) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if _line_width(word, font, stroke_width) <= max_width:
            current = word
        else:
            pieces = _break_word(word, font, max_width, stroke_width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
    if current:
        lines.append(current)
    return lines


def _measure_block(
    lines: list[str], font: ImageFont.FreeTypeFont, preset: StylePreset
) -> tuple[int, int, int]:
    """Measure a wrapped block: ``(width, height, line_step)`` in pixels (stroke included)."""
    line_height = _line_height(font, preset.stroke_width)
    line_step = round(line_height * preset.line_spacing)
    width = max((round(_line_width(line, font, preset.stroke_width)) for line in lines), default=0)
    height = line_step * (len(lines) - 1) + line_height if lines else 0
    return width, height, line_step


def _wrap_variable(
    text: str,
    font: ImageFont.FreeTypeFont,
    cap_for: Callable[[int], float],
    stroke_width: int,
) -> list[str]:
    """Greedily wrap ``text`` where line ``i`` may use up to ``cap_for(i)`` pixels (SG-6).

    Like :func:`wrap_text` but the width budget varies per line, so text follows a bubble that is
    narrow at the top and wide in the middle. Any word too wide for its line's cap is hard-broken.
    """
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        cap = cap_for(len(lines))
        candidate = f"{current} {word}" if current else word
        if _line_width(candidate, font, stroke_width) <= cap:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        cap = cap_for(len(lines))
        if _line_width(word, font, stroke_width) <= cap:
            current = word
        else:
            pieces = _break_word(word, font, max(1.0, cap), stroke_width)
            lines.extend(pieces[:-1])
            current = pieces[-1]
    if current:
        lines.append(current)
    return lines


# How many fixed-point passes to settle the line count when fitting to a polygon. The wrapped line
# count and the per-line widths depend on each other (a vertically-centred block samples different
# bands), so we iterate a few times; it converges quickly and the bound keeps it deterministic.
_SHAPE_PASSES = 4


def _fit_shaped(
    text: str,
    box: BBox,
    preset: StylePreset,
    polygon: Sequence[Point],
    load_font: FontLoader,
) -> TextLayout | None:
    """Fit ``text`` to the bubble *shape* given by ``polygon`` (SG-6), or ``None`` if not feasible.

    The polygon is taken in the same space as ``box``; each candidate font size lays the wrapped
    block out vertically-centred in the box and wraps each line to the polygon's interior width at
    that line's height, settling the line count over a few passes. The largest size whose block sits
    inside the polygon wins; if none do, the smallest size is returned with ``overflow`` set.
    """
    local = [Point(x=p.x - box.x, y=p.y - box.y) for p in polygon]
    pad = preset.padding
    avail_h = box.height - 2 * pad

    def bands_for(
        lines: list[str], line_height: int, line_step: int
    ) -> tuple[float, list[tuple[float, float]]]:
        """The block's top y (vertically centred) and each line's interior span for these lines."""
        n = len(lines)
        block_h = line_step * (n - 1) + line_height if n else 0
        y_start = pad + max(0.0, (avail_h - block_h) / 2)
        bands: list[tuple[float, float]] = []
        for i in range(n):
            top = y_start + i * line_step
            span = band_inner(local, top, top + line_height)
            bands.append((0.0, 0.0) if span is None else span)
        return y_start, bands

    best: TextLayout | None = None
    for size in range(preset.max_size, preset.min_size - 1, -1):
        font = load_font(preset.font_path, size)
        line_height = _line_height(font, preset.stroke_width)
        line_step = round(line_height * preset.line_spacing)

        lines = wrap_text(text, font, max(1.0, box.width - 2 * pad), preset.stroke_width)
        for _ in range(_SHAPE_PASSES):
            _, bands = bands_for(lines, line_height, line_step)

            def cap_for(i: int, _bands: list[tuple[float, float]] = bands) -> float:
                left, right = _bands[i] if i < len(_bands) else _bands[-1]
                return right - left - 2 * pad

            new_lines = _wrap_variable(text, font, cap_for, preset.stroke_width)
            if new_lines == lines:
                break
            lines = new_lines

        # Recompute bands for the final line set so bands and lines always line up.
        y_start, bands = bands_for(lines, line_height, line_step)
        n = len(lines)
        block_h = line_step * (n - 1) + line_height if n else 0
        widths = [_line_width(line, font, preset.stroke_width) for line in lines]
        fits = (
            block_h <= avail_h
            and all(right - left - 2 * pad > 0 for left, right in bands)
            and all(w <= (bands[i][1] - bands[i][0] - 2 * pad) for i, w in enumerate(widths))
        )
        layout = TextLayout(
            text=text,
            box=box,
            preset=preset,
            lines=tuple(lines),
            font_size=size,
            line_height=line_height,
            line_step=line_step,
            text_width=round(max(widths, default=0)),
            text_height=block_h,
            overflow=not fits,
            line_bands=tuple(bands),
            y_start=y_start,
        )
        if fits:
            return layout
        best = layout

    return best


def fit_text(
    text: str,
    box: BBox,
    preset: StylePreset,
    *,
    polygon: Sequence[Point] | None = None,
    load_font: FontLoader = load_font,
) -> TextLayout:
    """Find the largest font size at which ``text`` fits ``box`` and lay it out (FR-34, NFR-3).

    The search shrinks from ``preset.max_size`` to ``preset.min_size``; the first size whose wrapped
    block fits the padded box wins. If none fit, the smallest size is used and ``overflow`` is set.

    When ``polygon`` is given (the region's bubble outline, SG-6), the fit follows the bubble shape
    rather than the bounding box, so text stays inside round/irregular bubbles. A region with no
    polygon uses the box path unchanged, so its render is byte-identical.
    """
    if polygon is not None and len(polygon) >= 3:
        shaped = _fit_shaped(text, box, preset, polygon, load_font)
        if shaped is not None:
            return shaped
        # Fall back to the box fit if the polygon was degenerate/unusable.

    avail_w = box.width - 2 * preset.padding
    avail_h = box.height - 2 * preset.padding

    best: TextLayout | None = None
    for size in range(preset.max_size, preset.min_size - 1, -1):
        font = load_font(preset.font_path, size)
        lines = wrap_text(text, font, max(1.0, avail_w), preset.stroke_width)
        width, height, line_step = _measure_block(lines, font, preset)
        layout = TextLayout(
            text=text,
            box=box,
            preset=preset,
            lines=tuple(lines),
            font_size=size,
            line_height=_line_height(font, preset.stroke_width),
            line_step=line_step,
            text_width=width,
            text_height=height,
            overflow=False,
        )
        if width <= avail_w and height <= avail_h:
            return layout
        best = layout  # remember the smallest tried, in case nothing fits

    assert best is not None  # the range always runs at least once (max_size >= min_size)
    return TextLayout(**{**best.__dict__, "overflow": True})


def render_layout(layout: TextLayout, *, load_font: FontLoader = load_font) -> Image.Image:
    """Paint a fitted :class:`TextLayout` onto a transparent RGBA tile the size of its box (FR-34).

    The tile is sized to ``layout.box`` so the compositing batch can paste it at the box's top-left.
    Lines are centred vertically and aligned horizontally per the preset; the stroke is drawn under
    the fill so the outline frames the glyphs. Rendering is deterministic (NFR-26).
    """
    width = max(1, round(layout.box.width))
    height = max(1, round(layout.box.height))
    tile = Image.new("RGBA", (width, height), _NONE)
    if not any(layout.lines):
        return tile

    preset = layout.preset
    font = load_font(preset.font_path, layout.font_size)
    draw = ImageDraw.Draw(tile)

    if layout.line_bands is not None and layout.y_start is not None:
        _draw_shaped(draw, layout, font)
        return tile

    avail_w = width - 2 * preset.padding
    y = preset.padding + max(0, (height - 2 * preset.padding - layout.text_height) // 2)
    for line in layout.lines:
        line_w = _line_width(line, font, preset.stroke_width)
        if preset.align == "left":
            x = float(preset.padding)
        elif preset.align == "right":
            x = preset.padding + (avail_w - line_w)
        else:
            x = preset.padding + (avail_w - line_w) / 2
        draw.text(
            (round(x), y),
            line,
            font=font,
            fill=preset.fill,
            stroke_width=preset.stroke_width,
            stroke_fill=preset.stroke_fill,
        )
        y += layout.line_step
    return tile


def _draw_shaped(
    draw: ImageDraw.ImageDraw, layout: TextLayout, font: ImageFont.FreeTypeFont
) -> None:
    """Paint a polygon (bubble) layout: each line aligned within its own interior band (SG-6)."""
    preset = layout.preset
    pad = preset.padding
    assert layout.line_bands is not None and layout.y_start is not None
    y = layout.y_start
    for line, (left, right) in zip(layout.lines, layout.line_bands, strict=True):
        line_w = _line_width(line, font, preset.stroke_width)
        inner_w = right - left - 2 * pad
        if preset.align == "left":
            x = left + pad
        elif preset.align == "right":
            x = left + pad + (inner_w - line_w)
        else:
            x = left + pad + (inner_w - line_w) / 2
        draw.text(
            (round(x), round(y)),
            line,
            font=font,
            fill=preset.fill,
            stroke_width=preset.stroke_width,
            stroke_fill=preset.stroke_fill,
        )
        y += layout.line_step


def typeset(
    text: str,
    box: BBox,
    preset: StylePreset,
    *,
    polygon: Sequence[Point] | None = None,
    load_font: FontLoader = load_font,
) -> Image.Image:
    """Fit ``text`` to ``box`` and render it: convenience over fit_text + render_layout (FR-34)."""
    layout = fit_text(text, box, preset, polygon=polygon, load_font=load_font)
    return render_layout(layout, load_font=load_font)
