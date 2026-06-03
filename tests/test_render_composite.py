"""Tests for compositing typeset translations onto a page (§7.6, §10.8; FR-34, MVP-9, NFR-26)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from mfo.core.geometry import BBox
from mfo.render import (
    Placement,
    composite_file,
    composite_page,
    get_preset,
)


def _blank(width: int, height: int) -> Image.Image:
    return Image.new("RGB", (width, height), (255, 255, 255))


def test_composite_page_paints_text_into_its_box() -> None:
    base = _blank(200, 100)
    placement = Placement(
        text="Hello", box=BBox(x=20, y=20, width=160, height=60), preset=get_preset("default")
    )

    result = composite_page(base, [placement])

    assert result.image.size == (200, 100)
    assert result.image.mode == "RGB"
    arr = np.asarray(result.image)
    # Dark glyph pixels were painted somewhere inside the box, not on the untouched margin.
    box_region = arr[20:80, 20:180]
    assert (box_region.min(axis=2) < 100).any()  # some dark text drawn in the box
    assert np.all(arr[0:10, 0:10] == 255)  # the top-left corner is left untouched (I-1 surround)


def test_composite_page_does_not_mutate_the_base() -> None:
    base = _blank(120, 60)
    before = np.asarray(base).copy()
    composite_page(
        base, [Placement("Hi", BBox(x=5, y=5, width=100, height=40), get_preset("default"))]
    )
    assert np.array_equal(np.asarray(base), before)  # the input image is read-only (I-1)


def test_composite_page_flags_overflow() -> None:
    base = _blank(40, 40)
    # A long string in a tiny box cannot fit even at the smallest size.
    result = composite_page(
        base,
        [
            Placement(
                "overflowing text here", BBox(x=0, y=0, width=8, height=8), get_preset("default")
            )
        ],
    )
    assert result.overflow == 1
    assert result.placed[0].overflow


def test_composite_page_is_deterministic() -> None:
    base = _blank(160, 120)
    placements = [
        Placement("First line", BBox(x=10, y=10, width=140, height=40), get_preset("default")),
        Placement("Second", BBox(x=10, y=60, width=140, height=40), get_preset("shout")),
    ]
    first = composite_page(base, placements)
    second = composite_page(base, placements)
    assert first.image.tobytes() == second.image.tobytes()  # NFR-26


def test_composite_file_reads_base_and_returns_png(tmp_path: Path) -> None:
    base_path = tmp_path / "masked.png"
    before = np.asarray(_blank(120, 80)).copy()
    Image.fromarray(before, mode="RGB").save(base_path)

    artifact = composite_file(
        base_path, [Placement("Hi", BBox(x=10, y=10, width=100, height=60), get_preset("default"))]
    )

    assert artifact.render_png[:8] == b"\x89PNG\r\n\x1a\n"
    assert artifact.metadata["placements"] == 1
    assert artifact.metadata["size"] == [120, 80]
    # The base file on disk is untouched (I-1).
    assert np.array_equal(np.asarray(Image.open(base_path).convert("RGB")), before)


def test_composite_file_is_byte_stable(tmp_path: Path) -> None:
    base_path = tmp_path / "masked.png"
    _blank(100, 100).save(base_path)
    placements = [Placement("Same", BBox(x=5, y=5, width=90, height=90), get_preset("default"))]
    assert (
        composite_file(base_path, placements).render_png
        == composite_file(base_path, placements).render_png
    )
