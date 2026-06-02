"""OCR adapters (spec §10.4; FR-6, FR-12, FR-13, FR-15; NFR-17; MVP-4).

OCR is pluggable behind the :class:`OCREngine` protocol so Tesseract/PaddleOCR adapters can be
added later without touching the pipeline. The default :class:`MangaOcrEngine` wraps
`manga-ocr <https://github.com/kha-white/manga-ocr>`_ — the best offline Japanese (incl. vertical)
manga recognizer (Tech decision §19). It is an **optional** dependency (``pip install 'mfo[ocr]'``)
and the model loads lazily on first use, so importing this module never pulls in torch/transformers
and the rest of the pipeline keeps working without it.

Engines recognize a single region *crop* (a NumPy array in source-image pixel space) and return a
:class:`RecognizedText`. The storage layer turns these into ``OCRSpan`` records linked to their
region, kept separate from translation (FR-15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from mfo.core.geometry import BBox

Uint8Array = NDArray[np.uint8]


@dataclass(frozen=True)
class RecognizedText:
    """Text recognized for one region, with optional confidence and alternatives (FR-12, FR-13)."""

    text: str
    confidence: float | None = None
    alternatives: list[str] = field(default_factory=list)


class OCREngine(Protocol):
    """A swappable OCR engine (NFR-17). ``name``/``version`` identify it for caching."""

    name: str
    version: str

    def recognize(self, image: Uint8Array) -> RecognizedText: ...


class OcrDependencyError(RuntimeError):
    """Raised when an OCR engine's optional dependency or model is unavailable (I-7)."""


class MangaOcrEngine:
    """Offline Japanese OCR via manga-ocr (vertical text handled natively). Model loads lazily."""

    name = "manga-ocr"
    version = "1"  # adapter version; bump if the underlying model identity changes

    def __init__(self) -> None:
        self._model: object | None = None

    def _ensure_model(self) -> object:
        if self._model is None:
            try:
                from manga_ocr import MangaOcr
            except ImportError as exc:  # optional dependency not installed
                raise OcrDependencyError(
                    "manga-ocr is not installed; install it with:  pip install 'mfo[ocr]'"
                ) from exc
            self._model = MangaOcr()
        return self._model

    def recognize(self, image: Uint8Array) -> RecognizedText:
        model = self._ensure_model()
        text = str(model(Image.fromarray(image)))  # type: ignore[operator]
        # manga-ocr emits a single best transcription without scores or alternates.
        return RecognizedText(text=text, confidence=None, alternatives=[])


def manga_ocr_engine() -> OCREngine:
    return MangaOcrEngine()


_FACTORIES = {"manga-ocr": manga_ocr_engine}


def get_ocr_engine(name: str = "manga-ocr") -> OCREngine:
    """Resolve an OCR engine by config name (NFR-17). Raises ``ValueError`` if unknown."""
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"unknown OCR engine {name!r}; available: {known}") from None
    return factory()


def _crop(image: Image.Image, bbox: BBox) -> Image.Image:
    """Crop ``bbox`` from ``image`` (source-pixel coords), clamped to the image bounds."""
    left = max(0, int(round(bbox.x)))
    top = max(0, int(round(bbox.y)))
    right = min(image.width, int(round(bbox.x + bbox.width)))
    bottom = min(image.height, int(round(bbox.y + bbox.height)))
    # Degenerate boxes would crop to nothing; keep at least a 1px region.
    right = max(right, left + 1)
    bottom = max(bottom, top + 1)
    return image.crop((left, top, right, bottom))


def recognize_file(path: Path, bbox: BBox, engine: OCREngine) -> RecognizedText:
    """Open the image at ``path`` (read-only, I-1), crop ``bbox``, and recognize it."""
    with Image.open(path) as image:
        crop = _crop(image, bbox).convert("RGB")
    array = np.asarray(crop, dtype=np.uint8)
    return engine.recognize(array)
