"""Composite typeset translations onto a (masked) page (§7.6, §10.8; FR-34, MVP-9, NFR-26).

Pure and storage-free. This is the last render step: given a page image (normally the *masked*
layer from :mod:`mfo.render.mask`, with the source text already removed) and a list of
:class:`Placement`s — each a translated string, the box it belongs in, and the style to set it in —
it fits and paints every string into its box and returns the finished page.

Each placement is typeset with :func:`mfo.render.typeset.fit_text` (largest size that fits, wrapped
to the box) and pasted with its own alpha as the mask, so the glyphs blend onto the page and the
transparent surround leaves the art untouched. The original image is never mutated (I-1); the same
page + placements always yield byte-identical output (NFR-26). Whether any placement overflowed its
box is carried back so the caller can keep that uncertainty visible (I-4).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from mfo.core.geometry import BBox, Point
from mfo.render.typeset import (
    FontLoader,
    StylePreset,
    TextLayout,
    fit_text,
    load_font,
    render_layout,
)


@dataclass(frozen=True)
class Placement:
    """A translated string to set into ``box`` using ``preset`` (FR-34/35).

    ``polygon`` is the region's bubble outline (when known); given it, the text is fit to the bubble
    *shape* rather than the box, so it stays inside round/irregular bubbles (SG-6). ``None`` keeps a
    plain box fit.
    """

    text: str
    box: BBox
    preset: StylePreset
    polygon: tuple[Point, ...] | None = None


@dataclass(frozen=True)
class PlacedText:
    """The result of placing one :class:`Placement`: its fitted layout (carrying overflow)."""

    placement: Placement
    layout: TextLayout

    @property
    def overflow(self) -> bool:
        return self.layout.overflow


@dataclass(frozen=True)
class CompositeResult:
    """A composited page image and the per-placement layouts that produced it."""

    image: Image.Image
    placed: tuple[PlacedText, ...]

    @property
    def overflow(self) -> int:
        """How many placements could not fit their box even at the smallest size (I-4)."""
        return sum(1 for p in self.placed if p.overflow)


def composite_page(
    base: Image.Image,
    placements: list[Placement],
    *,
    load_font: FontLoader = load_font,
) -> CompositeResult:
    """Typeset and paint every placement onto a copy of ``base``; return the page + layouts.

    ``base`` is treated as read-only (a copy is drawn on). Placements are painted in order, each
    pasted at its box's top-left using the tile's alpha as the mask, so out-of-bounds tiles clip
    cleanly and the surrounding art is preserved.
    """
    canvas = base.convert("RGB")
    placed: list[PlacedText] = []
    for placement in placements:
        layout = fit_text(
            placement.text,
            placement.box,
            placement.preset,
            polygon=placement.polygon,
            load_font=load_font,
        )
        tile = render_layout(layout, load_font=load_font)
        canvas.paste(tile, (round(placement.box.x), round(placement.box.y)), tile)
        placed.append(PlacedText(placement, layout))
    return CompositeResult(image=canvas, placed=tuple(placed))


@dataclass(frozen=True)
class CompositeArtifact:
    """The PNG bytes of a composited page, the overflow count, and describing metadata."""

    render_png: bytes
    overflow: int
    metadata: dict[str, Any]


def composite_file(
    base_path: Path,
    placements: list[Placement],
    *,
    load_font: FontLoader = load_font,
) -> CompositeArtifact:
    """Read the page at ``base_path`` (read-only) and return its composited PNG bytes (I-1)."""
    with Image.open(base_path) as image:
        image.load()
        result = composite_page(image, placements, load_font=load_font)

    buffer = io.BytesIO()
    result.image.save(buffer, format="PNG")
    return CompositeArtifact(
        render_png=buffer.getvalue(),
        overflow=result.overflow,
        metadata={
            "placements": len(placements),
            "overflow": result.overflow,
            "size": [result.image.width, result.image.height],
        },
    )
