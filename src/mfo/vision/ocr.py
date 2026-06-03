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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

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


def manga_ocr_engine(lang: str | None = None) -> OCREngine:
    # manga-ocr is Japanese-only, so the source language is irrelevant here.
    return MangaOcrEngine()


# Map mfo source-language codes onto the model names PaddleOCR expects. Anything unknown is passed
# through as-is so a caller can name a paddle language directly.
_PADDLE_LANG = {"ja": "japan", "zh": "ch", "zh-cn": "ch", "en": "en", "ko": "korean"}


def _paddle_lines(raw: object) -> list[tuple[str, float | None]]:
    """Flatten PaddleOCR's nested ``[[ [box, (text, score)], ... ]]`` output into (text, score)s.

    Defensive about shape across paddle versions: only ``[box, (text, score)]`` entries whose
    payload starts with a string are accepted, so a box-only or rec-less result is simply ignored.
    """
    if not raw:
        return []
    pages: list[Any] = raw if isinstance(raw, list) else [raw]
    lines: list[tuple[str, float | None]] = []
    for page in pages:
        if not page:
            continue
        for entry in page:
            if not (isinstance(entry, list | tuple) and len(entry) >= 2):
                continue
            payload = entry[1]
            if not (
                isinstance(payload, list | tuple)
                and len(payload) >= 2
                and isinstance(payload[0], str)
            ):
                continue
            score = payload[1]
            lines.append((payload[0], float(score) if score is not None else None))
    return lines


class PaddleOcrEngine:
    """Offline OCR via PaddleOCR (JP/ZH/EN/KO). Model loads lazily on first use.

    PaddleOCR recognizes whole crops line-by-line; we join the lines top-to-bottom into one
    transcription and average the per-line scores into a single confidence (I-4). It is an
    **optional** dependency (``pip install 'mfo[ocr-paddle]'``).
    """

    name = "paddleocr"
    version = "1"  # adapter version; bump if the underlying model identity changes

    def __init__(self, lang: str | None = None) -> None:
        code = (lang or "ja").lower()
        self._lang = _PADDLE_LANG.get(code, code)
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:  # optional dependency not installed
                raise OcrDependencyError(
                    "paddleocr is not installed; install it with:  pip install 'mfo[ocr-paddle]'"
                ) from exc
            self._model = PaddleOCR(use_angle_cls=True, lang=self._lang, show_log=False)
        return self._model

    def recognize(self, image: Uint8Array) -> RecognizedText:
        model = self._ensure_model()
        lines = _paddle_lines(model.ocr(image, cls=True))
        text = "\n".join(text for text, _ in lines)
        scores = [score for _, score in lines if score is not None]
        confidence = round(sum(scores) / len(scores), 3) if scores else None
        return RecognizedText(text=text, confidence=confidence, alternatives=[])


def paddle_ocr_engine(lang: str | None = None) -> OCREngine:
    return PaddleOcrEngine(lang=lang)


_FACTORIES: dict[str, Callable[..., OCREngine]] = {
    "manga-ocr": manga_ocr_engine,
    "paddleocr": paddle_ocr_engine,
}


def get_ocr_engine(name: str = "manga-ocr", *, lang: str | None = None) -> OCREngine:
    """Resolve an OCR engine by config name (NFR-17). Raises ``ValueError`` if unknown.

    ``lang`` (the project's source language) is forwarded to engines that need it (e.g. PaddleOCR);
    Japanese-only manga-ocr ignores it.
    """
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"unknown OCR engine {name!r}; available: {known}") from None
    return factory(lang=lang)


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
