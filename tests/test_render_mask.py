"""Tests for the pure text-masking primitives (§10.8; FR-31/32/33; I-1/I-6; NFR-26)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from mfo.core.geometry import BBox
from mfo.render import MaskConfig, estimate_background, mask_image, restore


def _page_with_ink() -> tuple[Image.Image, BBox]:
    """A white page with a black 'text' block in the middle and a line of art down the left edge."""
    arr = np.full((40, 40, 3), 255, dtype=np.uint8)
    arr[15:25, 15:25] = 0  # the text block we want removed
    arr[:, 0:2] = 0  # line art well outside any region
    return Image.fromarray(arr, mode="RGB"), BBox(x=15, y=15, width=10, height=10)


def test_masking_removes_text_within_the_region() -> None:
    image, box = _page_with_ink()
    masked, mask = mask_image(image, [box], MaskConfig(pad=0, border=4))

    masked_arr = np.asarray(masked)
    # The previously-black text block is gone (filled with the surrounding white background).
    assert np.all(masked_arr[15:25, 15:25] == 255)
    # The mask records exactly that the region was altered (FR-31).
    mask_arr = np.asarray(mask)
    assert np.all(mask_arr[15:25, 15:25] == 255)


def test_line_art_outside_regions_is_preserved() -> None:
    image, box = _page_with_ink()
    original = np.asarray(image).copy()
    masked, mask = mask_image(image, [box], MaskConfig(pad=0, border=4))

    masked_arr = np.asarray(masked)
    mask_arr = np.asarray(mask)
    # Line art down the left edge is byte-identical and never marked as masked (FR-33).
    assert np.array_equal(masked_arr[:, 0:2], original[:, 0:2])
    assert np.all(mask_arr[:, 0:2] == 0)


def test_background_reconstruction_uses_the_local_colour() -> None:
    # A blue bubble (not white) with black text inside: the fill should be blue, not white (FR-32).
    arr = np.zeros((30, 30, 3), dtype=np.uint8)
    arr[:, :] = (0, 0, 200)
    arr[12:18, 12:18] = 0
    image = Image.fromarray(arr, mode="RGB")
    box = BBox(x=12, y=12, width=6, height=6)

    masked, _mask = mask_image(image, [box], MaskConfig(pad=0, border=4))
    filled = np.asarray(masked)[12:18, 12:18]
    assert np.all(filled == (0, 0, 200))


def test_estimate_background_is_the_ring_median() -> None:
    arr = np.full((20, 20, 3), 200, dtype=np.uint8)
    arr[8:12, 8:12] = 0  # inner text, must be ignored
    assert estimate_background(arr, (8, 8, 12, 12), border=4) == (200, 200, 200)


def test_restore_recovers_the_original_exactly() -> None:
    image, box = _page_with_ink()
    original = np.asarray(image)
    masked, mask = mask_image(image, [box], MaskConfig(pad=0, border=4))

    recovered = restore(masked, mask, image)
    # Reversibility (I-6 / DoD): the original is fully recoverable from masked + mask.
    assert np.array_equal(np.asarray(recovered), original)


def test_empty_regions_leave_the_page_untouched() -> None:
    image, _box = _page_with_ink()
    original = np.asarray(image).copy()
    masked, mask = mask_image(image, [], MaskConfig())

    assert np.array_equal(np.asarray(masked), original)
    assert np.all(np.asarray(mask) == 0)


def test_masking_is_deterministic() -> None:
    image, box = _page_with_ink()
    first, _ = mask_image(image, [box], MaskConfig())
    second, _ = mask_image(image, [box], MaskConfig())
    assert np.array_equal(np.asarray(first), np.asarray(second))
