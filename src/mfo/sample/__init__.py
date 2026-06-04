"""A tiny synthetic sample dataset for a docs-only end-to-end trial run (spec §21; NFR-28).

mfo ships no copyrighted manga, but a newcomer still needs *something* to run the pipeline on. This
module draws a handful of deterministic synthetic "pages" — a bordered frame with a few text blocks
— that the offline baseline detector picks up, so ``mfo sample`` → ``init`` → ``import`` → ``run`` →
``export`` works on a clean machine with no downloads. The pages are generated (not committed
binaries) so they stay deterministic and add no weight to the repo.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

PAGE_SIZE = (800, 1200)

# A couple of lines of placeholder dialogue per page. Latin text keeps the sample dependency-free
# (no JP font needed just to render the fixture); the baseline detector keys off the dark blocks.
_PAGE_TEXT: tuple[tuple[str, ...], ...] = (
    ("HELLO THERE", "THIS IS A SAMPLE PAGE", "FOR THE MFO PIPELINE"),
    ("ONE BUBBLE", "PER REGION", "NICE AND TIDY"),
)


def _draw_page(lines: tuple[str, ...]) -> Image.Image:
    """Render one synthetic page: a page border plus stacked text blocks the detector will find."""
    image = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    width, height = PAGE_SIZE
    draw.rectangle((8, 8, width - 9, height - 9), outline="black", width=3)
    # Stack each line in its own bubble-ish rounded box so detection yields distinct regions.
    box_h = 140
    gap = 60
    top = 120
    for line in lines:
        box = (120, top, width - 120, top + box_h)
        draw.rounded_rectangle(box, radius=28, outline="black", width=4, fill="white")
        draw.text((160, top + box_h // 2 - 6), line, fill="black")
        top += box_h + gap
    return image


def create_sample_pages(dest: Path, *, count: int = 2) -> list[Path]:
    """Write ``count`` synthetic sample pages into ``dest`` and return their paths (deterministic).

    More pages than the catalog of canned text simply cycles through it, so any ``count`` works.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        lines = _PAGE_TEXT[index % len(_PAGE_TEXT)]
        path = dest / f"page-{index + 1:02d}.png"
        _draw_page(lines).save(path)
        paths.append(path)
    return paths


__all__ = ["PAGE_SIZE", "create_sample_pages"]
