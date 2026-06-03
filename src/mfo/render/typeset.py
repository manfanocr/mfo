"""Font fitting & text placement for the render stage (§10.8; FR-34/35, NFR-3; SG-6 groundwork).

Pure and storage-free. Given a translated string and the bounding box it must live in, this picks
the largest font size at which the text — wrapped to the box width — still fits the box height, then
lays the lines out with the requested alignment and stroke/outline. The result is a
:class:`TextLayout` (what to draw and where) and a :func:`render_layout` that paints it onto a
transparent RGBA tile the size of the box, ready for the compositing batch to paste onto a page.

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

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import cast

from PIL import Image, ImageDraw, ImageFont

from mfo.core.geometry import BBox

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


def fit_text(
    text: str,
    box: BBox,
    preset: StylePreset,
    *,
    load_font: FontLoader = load_font,
) -> TextLayout:
    """Find the largest font size at which ``text`` fits ``box`` and lay it out (FR-34, NFR-3).

    The search shrinks from ``preset.max_size`` to ``preset.min_size``; the first size whose wrapped
    block fits the padded box wins. If none fit, the smallest size is used and ``overflow`` is set.
    """
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


def typeset(
    text: str,
    box: BBox,
    preset: StylePreset,
    *,
    load_font: FontLoader = load_font,
) -> Image.Image:
    """Fit ``text`` to ``box`` and render it: convenience over fit_text + render_layout (FR-34)."""
    layout = fit_text(text, box, preset, load_font=load_font)
    return render_layout(layout, load_font=load_font)
