"""Tests for offline SFX region classification (SG-5)."""

from __future__ import annotations

from mfo.core.enums import RegionType
from mfo.core.geometry import BBox
from mfo.vision.sfx import (
    HeuristicSfxClassifier,
    SfxFeatures,
    classify_region_type,
    get_sfx_classifier,
)


def _features(box: BBox, region_type: RegionType = RegionType.UNKNOWN) -> SfxFeatures:
    return SfxFeatures(bbox=box, region_type=region_type, page_width=200, page_height=300)


def test_large_stretched_blob_is_sfx() -> None:
    # A wide streak covering a good chunk of the page reads as SFX.
    box = BBox(x=10, y=10, width=160, height=30)
    assert HeuristicSfxClassifier().is_sfx(_features(box)) is True


def test_compact_bubble_is_not_sfx() -> None:
    # A small, roughly-square box (a speech bubble) is left alone.
    box = BBox(x=10, y=10, width=30, height=28)
    assert HeuristicSfxClassifier().is_sfx(_features(box)) is False


def test_large_but_square_blob_is_not_sfx() -> None:
    # Big but square (a panel) — aspect ratio guards against false positives.
    box = BBox(x=0, y=0, width=120, height=120)
    assert HeuristicSfxClassifier().is_sfx(_features(box)) is False


def test_classify_only_promotes_unknown_regions() -> None:
    sfx_box = BBox(x=10, y=10, width=160, height=30)
    classifier = HeuristicSfxClassifier()
    # An UNKNOWN region matching the heuristic becomes SFX...
    assert classify_region_type(_features(sfx_box), classifier) is RegionType.SFX
    # ...but a detector's BUBBLE label is preserved even if the geometry looks SFX-like (I-3).
    assert (
        classify_region_type(_features(sfx_box, RegionType.BUBBLE), classifier) is RegionType.BUBBLE
    )


def test_get_sfx_classifier_resolves_builtin() -> None:
    assert get_sfx_classifier("heuristic").name == "heuristic"
