"""SFX (sound-effect) region classification (spec §10.3; SG-5; FR-11; NFR-17).

Onomatopoeia/SFX read very differently from dialogue — they are drawn art, often large and
stretched, rarely inside a bubble — so the pipeline benefits from telling them apart so it can
transliterate or leave them untouched rather than translating them like speech (SG-5). Trained
detectors already emit a :attr:`~mfo.core.enums.RegionType.SFX` class; this module adds a
**best-effort, offline** classifier so the baseline path can flag SFX too, with no model download.

Classification is pluggable behind the :class:`SfxClassifier` protocol (NFR-17): the offline default
:class:`HeuristicSfxClassifier` uses simple geometry, and a third-party model can register via the
``mfo.sfx_classifiers`` entry-point group (batch 8.3). A classifier only ever *promotes* an
otherwise-:attr:`~mfo.core.enums.RegionType.UNKNOWN` region to SFX — it never overrides a detector's
existing bubble/SFX label or a human edit (I-3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mfo.core.enums import RegionType
from mfo.core.geometry import BBox
from mfo.core.plugins import SFX_CLASSIFIER_GROUP, resolve_factory


@dataclass(frozen=True)
class SfxFeatures:
    """What a classifier sees: a region's box, its current type, and the page dimensions."""

    bbox: BBox
    region_type: RegionType
    page_width: int
    page_height: int


class SfxClassifier(Protocol):
    """A swappable SFX classifier (NFR-17). ``name``/``version`` identify it for caching."""

    name: str
    version: str

    def is_sfx(self, features: SfxFeatures) -> bool: ...


class HeuristicSfxClassifier:
    """Offline geometry heuristic: a large, stretched, non-bubble blob is likely SFX.

    SFX art tends to be big relative to the page and far from square (a long ``ドーン`` streak),
    unlike the compact, roughly-round speech bubbles dialogue lives in. So a region is called SFX
    when it covers at least ``min_area_frac`` of the page **and** its aspect ratio is more extreme
    than ``aspect_ratio`` (very wide or very tall). The thresholds are conservative so normal
    bubbles are never mistaken for SFX (dialogue handling stays unchanged).
    """

    name = "heuristic"
    version = "1"

    def __init__(self, *, min_area_frac: float = 0.02, aspect_ratio: float = 2.2) -> None:
        self.min_area_frac = min_area_frac  # at least 2% of the page area
        self.aspect_ratio = aspect_ratio  # width/height ≥ this, or ≤ its reciprocal

    def is_sfx(self, features: SfxFeatures) -> bool:
        box = features.bbox
        page_area = float(features.page_width * features.page_height)
        if page_area <= 0 or box.height <= 0 or box.width <= 0:
            return False
        if box.area / page_area < self.min_area_frac:
            return False
        ratio = box.width / box.height
        return ratio >= self.aspect_ratio or ratio <= 1.0 / self.aspect_ratio


def heuristic_sfx_classifier() -> SfxClassifier:
    """The offline default SFX classifier (no dependencies, no model download)."""
    return HeuristicSfxClassifier()


def classify_region_type(features: SfxFeatures, classifier: SfxClassifier) -> RegionType:
    """The region's type after classification: promote an UNKNOWN region to SFX, else leave it.

    Only :attr:`RegionType.UNKNOWN` regions are eligible — a detector's bubble/narration/SFX label
    and any human-assigned type are preserved (I-3).
    """
    if features.region_type is not RegionType.UNKNOWN:
        return features.region_type
    return RegionType.SFX if classifier.is_sfx(features) else RegionType.UNKNOWN


_FACTORIES: dict[str, Callable[..., SfxClassifier]] = {"heuristic": heuristic_sfx_classifier}


def get_sfx_classifier(name: str = "heuristic") -> SfxClassifier:
    """Resolve an SFX classifier by name: built-ins first, then ``mfo.sfx_classifiers`` plugins."""
    return resolve_factory(name, _FACTORIES, SFX_CLASSIFIER_GROUP, kind="SFX classifier")()
