"""Region detection adapters (spec §10.3; FR-10, FR-11; NFR-17, NFR-21; MVP-3).

Detection is pluggable behind the :class:`RegionDetector` protocol so heavier ML detectors can
be added later (batch 2.2) without touching the pipeline. The default
:class:`ConnectedComponentsDetector` is a dependency-light OpenCV baseline that runs CPU-only and
needs **no model download** (NFR-21), so the project works out of the box.

Detectors operate on a page as a NumPy array and return :class:`DetectedRegion` boxes in
source-image pixel space (origin top-left), matching :mod:`mfo.core.geometry`. The storage layer
turns these into persisted ``Region`` records linked to their page.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from mfo.core.enums import RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.vision._paddle import _prefer_paddle_cpu_runtime

Uint8Array = NDArray[np.uint8]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectedRegion:
    """A candidate text region in source-image pixel space.

    ``status`` lets a detector mark a box it does not trust (e.g. a panel-/frame-sized blob from the
    heuristic baseline) as :attr:`RegionStatus.IGNORE` rather than dropping it, so the box stays in
    the data (I-1/I-2) and editable but is skipped by OCR/translation/render and kept out of the
    review queue, instead of masquerading as a confident bubble (I-4). Most detections are
    :attr:`RegionStatus.AUTO`.
    """

    bbox: BBox
    type: RegionType
    confidence: float
    status: RegionStatus = RegionStatus.AUTO
    # Best-effort recognition from a det+rec detector (e.g. PaddleOCR's full pipeline). Left
    # ``None`` by detection-only detectors; when present the detect stage records it as a
    # provisional OCR span the OCR stage can adopt instead of re-recognizing (batch 8.0).
    # Uncertainty stays visible (I-4).
    text: str | None = None
    text_confidence: float | None = None


class RegionDetector(Protocol):
    """A swappable region detector (NFR-17). ``name``/``version`` identify it for caching."""

    name: str
    version: str

    def detect(self, image: Uint8Array) -> list[DetectedRegion]: ...


@dataclass(frozen=True)
class BaselineConfig:
    """Heuristic thresholds for the connected-components baseline.

    The baseline can't tell a speech bubble from a dense panel, so rather than trust every blob it
    keeps the doubtful ones but auto-marks them ``IGNORE`` (mostly whole panels/frames; see
    ``suspect_area_frac``/``wide_frac``). Only specks and near-page-spanning blobs are dropped
    outright.
    """

    min_area_frac: float = 0.0004  # ignore specks smaller than this fraction of the page
    max_area_frac: float = 0.85  # drop only near-page-spanning blobs (e.g. a whole-page scan)
    suspect_area_frac: float = 0.12  # bigger than a typical bubble → keep but auto-ignore
    wide_frac: float = 0.85  # blob spanning ~this fraction of the page width → a panel/frame
    close_frac: float = 0.015  # morphological-close kernel as a fraction of the short edge
    min_fill: float = 0.12  # min filled fraction of the bounding box to count as text


def _to_gray(image: Uint8Array) -> Uint8Array:
    if image.ndim == 2:
        return image
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), dtype=np.uint8)


def _classify(width: int, height: int) -> RegionType:
    """Coarse type guess from shape alone (best-effort; refined by the ML detector in 2.2)."""
    aspect = width / height
    if aspect >= 2.5:
        return RegionType.NARRATION  # wide rectangle → narration/caption box
    if aspect <= 0.4:
        return RegionType.SIDE_TEXT  # tall/vertical strip
    return RegionType.BUBBLE


def _confidence(fill: float, area_frac: float) -> float:
    """A bounded heuristic score: well-filled, plausibly-sized blobs score higher (I-4)."""
    size_score = 1.0 if 0.003 <= area_frac <= 0.25 else 0.6
    return round(min(1.0, (0.4 + 0.5 * fill) * size_score), 3)


class ConnectedComponentsDetector:
    """OpenCV connected-components baseline: threshold → merge glyphs → box the blobs."""

    name = "baseline-cc"
    version = "1"

    def __init__(self, config: BaselineConfig | None = None) -> None:
        self._config = config or BaselineConfig()

    def detect(self, image: Uint8Array) -> list[DetectedRegion]:
        config = self._config
        gray = _to_gray(image)
        height, width = gray.shape[:2]
        page_area = float(height * width)
        if page_area == 0:
            return []

        # Otsu binarization; invert so ink (text/outlines) becomes foreground.
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        # Close gaps between glyphs so a line/block of text becomes one component.
        kernel_size = max(1, round(min(height, width) * config.close_frac))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        merged = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(merged, connectivity=8)

        regions: list[DetectedRegion] = []
        for i in range(1, count):  # 0 is the background component
            x = int(stats[i, cv2.CC_STAT_LEFT])
            y = int(stats[i, cv2.CC_STAT_TOP])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            area = int(stats[i, cv2.CC_STAT_AREA])
            if w == 0 or h == 0:
                continue
            area_frac = area / page_area
            if area_frac < config.min_area_frac or area_frac > config.max_area_frac:
                continue
            fill = area / float(w * h)
            if fill < config.min_fill:
                continue
            # A speech bubble is small; a panel/frame blob is large or spans most of the page width.
            # Keep an oversized/wide blob (don't silently drop a possible region) but auto-mark it
            # IGNORE with a capped score (I-4): it is almost always panel art, so it is excluded
            # from OCR/translation/render and the review queue rather than passing as a bubble.
            suspicious = area_frac >= config.suspect_area_frac or (w / width) >= config.wide_frac
            confidence = _confidence(fill, area_frac)
            status = RegionStatus.AUTO
            if suspicious:
                confidence = round(min(confidence, 0.3), 3)
                status = RegionStatus.IGNORE
            regions.append(
                DetectedRegion(
                    bbox=BBox(x=float(x), y=float(y), width=float(w), height=float(h)),
                    type=_classify(w, h),
                    confidence=confidence,
                    status=status,
                )
            )
        regions.sort(key=lambda r: (r.bbox.y, r.bbox.x))
        return regions


def baseline_detector(lang: str | None = None) -> RegionDetector:
    return ConnectedComponentsDetector()


# --- ML detector adapter (batch 2.2; FR-11, FR-14; NFR-22) -------------------------------------
#
# A trained bubble/text detector (e.g. comic-text-detector / YOLO) gives better boxes and a real
# region-type classification than the heuristic baseline. It is **optional**: the heavyweight
# runtime (onnxruntime) and the model weights load lazily on first use, so importing this module
# never pulls them in and the offline core keeps working without them (I-7/I-8, NFR-21). When the
# dependency or model is absent, :class:`FallbackDetector` transparently uses the baseline so the
# pipeline never hard-fails (DoD 2.2).


class DetectorDependencyError(RuntimeError):
    """Raised when an ML detector's optional dependency or model is unavailable (I-7)."""


# Model class index → region type. Trained detectors emit a class id per box; this maps the common
# comic-text-detector / YOLO label order onto our taxonomy (FR-11, FR-14). Out-of-range ids fall
# back to UNKNOWN so a relabelled model never crashes the pipeline.
DEFAULT_CLASS_LABELS: tuple[RegionType, ...] = (
    RegionType.BUBBLE,
    RegionType.NARRATION,
    RegionType.SFX,
    RegionType.CAPTION,
)


def classify_region(
    class_index: int, labels: Sequence[RegionType] = DEFAULT_CLASS_LABELS
) -> RegionType:
    """Map a model class index to a :class:`RegionType`, defaulting to UNKNOWN if out of range."""
    if 0 <= class_index < len(labels):
        return labels[class_index]
    return RegionType.UNKNOWN


class DetectionModel(Protocol):
    """A loaded detection model: turns a page into candidate boxes in source-pixel space.

    This is the swap point for the actual inference runtime (ONNX/torch). Keeping it separate from
    :class:`MLDetector` lets the adapter's threshold/NMS/ordering logic be tested with a fake model
    and no heavyweight dependency.
    """

    def infer(self, image: Uint8Array) -> list[DetectedRegion]: ...


def _iou(a: BBox, b: BBox) -> float:
    """Intersection-over-union of two boxes (0 when disjoint)."""
    inter_w = max(0.0, min(a.right, b.right) - max(a.x, b.x))
    inter_h = max(0.0, min(a.bottom, b.bottom) - max(a.y, b.y))
    inter = inter_w * inter_h
    if inter == 0.0:
        return 0.0
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def non_max_suppression(
    regions: Sequence[DetectedRegion], iou_threshold: float
) -> list[DetectedRegion]:
    """Greedy NMS: keep the highest-confidence box, drop those overlapping it beyond the IoU cap."""
    kept: list[DetectedRegion] = []
    for region in sorted(regions, key=lambda r: r.confidence, reverse=True):
        if all(_iou(region.bbox, k.bbox) <= iou_threshold for k in kept):
            kept.append(region)
    return kept


def default_model_dir() -> Path:
    """Where ML model weights are cached (overridable via the ``MFO_MODEL_DIR`` env var)."""
    override = os.environ.get("MFO_MODEL_DIR")
    return Path(override) if override else Path.home() / ".cache" / "mfo" / "models"


@dataclass(frozen=True)
class MLDetectorConfig:
    """Configuration for the ML detector adapter.

    ``model_url`` is empty by default so nothing is downloaded implicitly: point it at an ONNX
    export (or drop the file into ``model_dir``) to enable the detector. GPU is opt-in by prepending
    a provider, e.g. ``providers=("CUDAExecutionProvider", "CPUExecutionProvider")``.
    """

    model_url: str = ""
    model_filename: str = "comic-text-detector.onnx"
    model_dir: Path | None = None  # None → default_model_dir()
    input_size: int = 1024
    score_threshold: float = 0.3
    nms_iou: float = 0.45
    providers: tuple[str, ...] = ("CPUExecutionProvider",)
    class_labels: tuple[RegionType, ...] = DEFAULT_CLASS_LABELS

    def resolved_model_dir(self) -> Path:
        return self.model_dir or default_model_dir()


def _letterbox(image: Uint8Array, size: int) -> tuple[Uint8Array, float, float, float]:
    """Resize ``image`` to fit a ``size``×``size`` square keeping aspect; return scale and padding.

    Returns ``(padded, scale, pad_x, pad_y)`` where a model-space coordinate maps back to source
    pixels via ``(coord - pad) / scale``.
    """
    height, width = image.shape[:2]
    scale = size / max(height, width) if max(height, width) > 0 else 1.0
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    pad_x, pad_y = (size - new_w) / 2.0, (size - new_h) / 2.0
    channels = 1 if image.ndim == 2 else image.shape[2]
    canvas = np.zeros((size, size, channels), dtype=np.uint8)
    top, left = int(pad_y), int(pad_x)
    canvas[top : top + new_h, left : left + new_w] = resized.reshape(new_h, new_w, channels)
    return canvas, scale, pad_x, pad_y


def decode_detections(
    rows: NDArray[np.float32],
    *,
    scale: float,
    pad_x: float,
    pad_y: float,
    labels: Sequence[RegionType] = DEFAULT_CLASS_LABELS,
) -> list[DetectedRegion]:
    """Decode raw ``[N, 6]`` detections ``(x1, y1, x2, y2, score, class)`` to source-space boxes.

    Coordinates are in letterboxed model space and are un-padded/un-scaled back to source pixels.
    Pure (no model/runtime) so it carries real test coverage.
    """
    detections: list[DetectedRegion] = []
    for row in rows:
        x1, y1, x2, y2, score, cls = (float(v) for v in row[:6])
        left = (min(x1, x2) - pad_x) / scale
        top = (min(y1, y2) - pad_y) / scale
        width = abs(x2 - x1) / scale
        height = abs(y2 - y1) / scale
        detections.append(
            DetectedRegion(
                bbox=BBox(x=left, y=top, width=max(0.0, width), height=max(0.0, height)),
                type=classify_region(int(round(cls)), labels),
                confidence=round(min(1.0, max(0.0, score)), 3),
            )
        )
    return detections


def _clamp_bbox(bbox: BBox, width: int, height: int) -> BBox | None:
    """Clamp a box to the page; return None if it collapses below 1px (degenerate)."""
    left = min(max(0.0, bbox.x), float(width))
    top = min(max(0.0, bbox.y), float(height))
    right = min(max(0.0, bbox.right), float(width))
    bottom = min(max(0.0, bbox.bottom), float(height))
    if right - left < 1.0 or bottom - top < 1.0:
        return None
    return BBox(x=left, y=top, width=right - left, height=bottom - top)


class OnnxDetectionModel:
    """ONNX-runtime detection model: imports onnxruntime lazily and fetches weights on demand.

    Raises :class:`DetectorDependencyError` (caught by :class:`FallbackDetector`) when onnxruntime
    is not installed or the model file is neither present nor downloadable.
    """

    def __init__(self, config: MLDetectorConfig) -> None:
        self._config = config
        self._session: object | None = None

    def _model_path(self) -> Path:
        return self._config.resolved_model_dir() / self._config.model_filename

    def ensure_model_file(self) -> Path:
        """Resolve the model file, downloading it from ``model_url`` (atomically) if absent."""
        path = self._model_path()
        if path.exists():
            return path
        if not self._config.model_url:
            raise DetectorDependencyError(
                f"detector model not found at {path}; set MLDetectorConfig.model_url to "
                "download it or place the .onnx file there"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        try:
            urllib.request.urlretrieve(self._config.model_url, tmp)  # noqa: S310 (user-configured)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise DetectorDependencyError(
                f"failed to download detector model from {self._config.model_url}: {exc}"
            ) from exc
        tmp.replace(path)  # atomic publish (NFR-26/27)
        return path

    def _ensure_session(self) -> object:
        if self._session is None:
            try:
                import onnxruntime as ort
            except ImportError as exc:  # optional dependency not installed
                raise DetectorDependencyError(
                    "onnxruntime is not installed; install it with:  pip install 'mfo[detect]'"
                ) from exc
            path = self.ensure_model_file()
            self._session = ort.InferenceSession(str(path), providers=list(self._config.providers))
        return self._session

    def infer(self, image: Uint8Array) -> list[DetectedRegion]:
        session = self._ensure_session()
        padded, scale, pad_x, pad_y = _letterbox(image, self._config.input_size)
        # NCHW float tensor in [0, 1]; the common export expects RGB.
        tensor = padded.astype(np.float32).transpose(2, 0, 1)[np.newaxis] / 255.0
        input_name = session.get_inputs()[0].name  # type: ignore[attr-defined]
        outputs = session.run(None, {input_name: tensor})  # type: ignore[attr-defined]
        rows = np.asarray(outputs[0], dtype=np.float32).reshape(-1, 6)
        return decode_detections(
            rows, scale=scale, pad_x=pad_x, pad_y=pad_y, labels=self._config.class_labels
        )


class MLDetector:
    """Trained detector adapter: runs a :class:`DetectionModel`, then thresholds + NMS + orders."""

    name = "ml-detector"
    version = "1"

    def __init__(
        self, config: MLDetectorConfig | None = None, model: DetectionModel | None = None
    ) -> None:
        self._config = config or MLDetectorConfig()
        self._model = model  # injectable; the ONNX model is built lazily on first use

    def _get_model(self) -> DetectionModel:
        if self._model is None:
            self._model = OnnxDetectionModel(self._config)
        return self._model

    def detect(self, image: Uint8Array) -> list[DetectedRegion]:
        height, width = image.shape[:2]
        candidates = [
            region
            for region in self._get_model().infer(image)
            if region.confidence >= self._config.score_threshold
        ]
        kept = non_max_suppression(candidates, self._config.nms_iou)
        regions: list[DetectedRegion] = []
        for region in kept:
            bbox = _clamp_bbox(region.bbox, width, height)
            if bbox is not None:
                regions.append(
                    DetectedRegion(bbox=bbox, type=region.type, confidence=region.confidence)
                )
        regions.sort(key=lambda r: (r.bbox.y, r.bbox.x))
        return regions


class FallbackDetector:
    """Tries ``primary``; if its model/dependency is unavailable, uses ``fallback`` (DoD 2.2).

    Resolution happens once, lazily, on the first :meth:`detect`, then is pinned. The reported
    ``name``/``version`` is a stable composite so the detection cache signature (NFR-8) is
    deterministic regardless of which backend ends up running.
    """

    def __init__(self, primary: RegionDetector, fallback: RegionDetector) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+fallback"
        self.version = f"{primary.version}+{fallback.version}"
        self._resolved: RegionDetector | None = None

    def detect(self, image: Uint8Array) -> list[DetectedRegion]:
        if self._resolved is not None:
            return self._resolved.detect(image)
        try:
            regions = self.primary.detect(image)
        except DetectorDependencyError as exc:
            _log.warning(
                "%s detector unavailable (%s); falling back to %s",
                self.primary.name,
                exc,
                self.fallback.name,
            )
            self._resolved = self.fallback
            return self.fallback.detect(image)
        self._resolved = self.primary
        return regions


def ml_detector(
    config: MLDetectorConfig | None = None, *, lang: str | None = None
) -> RegionDetector:
    """The ML detector with a transparent baseline fallback (the ``"ml"`` config name)."""
    return FallbackDetector(MLDetector(config), ConnectedComponentsDetector())


# --- PaddleOCR text detector adapter (FR-11; NFR-17) ------------------------------------------
#
# PaddleOCR ships a text-detection model that returns tight quads around text; used detection-only
# it is a strong alternative to the connected-components baseline (boxes text, not panels). Optional
# (``pip install 'mfo[ocr-paddle]'``) and lazy, so importing this module never pulls paddle in; when
# absent, :func:`paddle_detector` falls back to the baseline so the pipeline never hard-fails.


def _paddle_boxes(raw: object) -> list[Any]:
    """Flatten PaddleOCR 3.x detection output into a flat list of polygons (defensively).

    ``TextDetection.predict`` returns one dict-like result per image, each carrying ``dt_polys``
    (a list of point arrays). We stay tolerant of shape (NumPy arrays or nested lists, missing
    keys) so a malformed result is simply ignored rather than crashing.
    """
    if not raw:
        return []
    results: list[Any] = raw if isinstance(raw, list) else [raw]
    boxes: list[Any] = []
    for result in results:
        try:
            polys = result["dt_polys"]
        except (KeyError, TypeError, IndexError):
            continue
        if polys is None:
            continue
        for poly in polys:
            points = [
                (float(point[0]), float(point[1]))
                for point in poly
                if len(point) >= 2  # NumPy rows and lists both index/len the same way
            ]
            if len(points) >= 3:
                boxes.append(points)
    return boxes


class PaddleDetector:
    """Text-box detector backed by PaddleOCR 3.x's standalone ``TextDetection`` model.

    Returns one :class:`DetectedRegion` per detected text quad (typed ``UNKNOWN`` — paddle does not
    classify bubble vs. caption). Raises :class:`DetectorDependencyError` (caught by
    :class:`FallbackDetector`) when paddle or its ``paddlepaddle`` backend is unavailable.
    """

    name = "paddle-det"
    version = "1"

    def __init__(self) -> None:
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            _prefer_paddle_cpu_runtime()
            try:
                from paddleocr import TextDetection
            except ImportError as exc:  # optional dependency not installed
                raise DetectorDependencyError(
                    "paddleocr is not installed; install it with:  pip install 'mfo[ocr-paddle]'"
                ) from exc
            try:
                self._model = TextDetection()
            except Exception as exc:  # missing paddlepaddle backend, model download, etc.
                raise DetectorDependencyError(
                    "PaddleOCR could not initialize; install its inference backend with: "
                    " pip install 'mfo[ocr-paddle]'"
                ) from exc
        return self._model

    def detect(self, image: Uint8Array) -> list[DetectedRegion]:
        model = self._ensure_model()
        regions: list[DetectedRegion] = []
        for box in _paddle_boxes(model.predict(image)):
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            if x1 - x0 < 1.0 or y1 - y0 < 1.0:
                continue
            regions.append(
                DetectedRegion(
                    bbox=BBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0),
                    type=RegionType.UNKNOWN,
                    confidence=0.9,  # paddle's det model is reliable but emits no per-box score
                )
            )
        regions.sort(key=lambda r: (r.bbox.y, r.bbox.x))
        return regions


def paddle_detector(lang: str | None = None) -> RegionDetector:
    """The PaddleOCR text detector with a transparent baseline fallback (the ``"paddle"`` name)."""
    return FallbackDetector(PaddleDetector(), ConnectedComponentsDetector())


# --- Fused PaddleOCR detect+recognize adapter (FR-12; NFR-7/8/17; I-4) ------------------------
#
# PaddleOCR's full pipeline detects *and* recognizes in one pass, so when paddle is used for both
# stages, recognizing again in the OCR stage repeats work. This detector runs that pipeline once and
# carries the recognized text + per-box score on each region; the detect/OCR storage stages then
# let the OCR stage adopt the text instead of re-running paddle (batch 8.0). Optional + lazy like
# the others; falls back to the baseline (which yields no text, so OCR runs normally) if paddle is
# absent.


def _paddle_rec_items(raw: object) -> list[tuple[list[Any], str, float | None]]:
    """Flatten PaddleOCR 3.x full-pipeline output into ``(polygon, text, score)`` triples.

    ``PaddleOCR.predict`` returns one dict-like result per image carrying parallel ``rec_polys``
    (or ``dt_polys``), ``rec_texts`` and ``rec_scores``. We stay tolerant of shape (missing keys,
    ragged lengths, non-string text) so a malformed result is ignored rather than crashing.
    """
    if not raw:
        return []
    results: list[Any] = raw if isinstance(raw, list) else [raw]
    items: list[tuple[list[Any], str, float | None]] = []
    for result in results:
        polys: Any = None
        for key in ("rec_polys", "dt_polys"):
            try:
                polys = result[key]
            except (KeyError, TypeError, IndexError):
                continue
            if polys is not None:
                break
        if polys is None:
            continue
        try:
            texts = list(result["rec_texts"])
        except (KeyError, TypeError, IndexError):
            continue
        try:
            scores = list(result["rec_scores"])
        except (KeyError, TypeError, IndexError):
            scores = []
        for i, poly in enumerate(polys):
            if i >= len(texts) or not isinstance(texts[i], str):
                continue
            points = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
            if len(points) < 3:
                continue
            score = scores[i] if i < len(scores) else None
            items.append((points, texts[i], float(score) if score is not None else None))
    return items


class PaddleRecDetector:
    """PaddleOCR full pipeline (detect **and** recognize) as a detector, capturing text per box.

    Each returned :class:`DetectedRegion` carries the recognized ``text`` and ``text_confidence``
    so the OCR stage can reuse it (batch 8.0). Raises :class:`DetectorDependencyError` (caught by
    :class:`FallbackDetector`) when paddle or its ``paddlepaddle`` backend is unavailable.
    """

    name = "paddle-rec"
    version = "1"

    def __init__(self, lang: str | None = None) -> None:
        from mfo.vision.ocr import _PADDLE_LANG

        code = (lang or "ja").lower()
        self._lang = _PADDLE_LANG.get(code, code)
        self._model: Any = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            _prefer_paddle_cpu_runtime()
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:  # optional dependency not installed
                raise DetectorDependencyError(
                    "paddleocr is not installed; install it with:  pip install 'mfo[ocr-paddle]'"
                ) from exc
            try:
                self._model = PaddleOCR(
                    lang=self._lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except Exception as exc:  # missing paddlepaddle backend, bad lang, etc.
                raise DetectorDependencyError(
                    "PaddleOCR could not initialize; install its inference backend with: "
                    " pip install 'mfo[ocr-paddle]'"
                ) from exc
        return self._model

    def detect(self, image: Uint8Array) -> list[DetectedRegion]:
        model = self._ensure_model()
        regions: list[DetectedRegion] = []
        for points, text, score in _paddle_rec_items(model.predict(image)):
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            if x1 - x0 < 1.0 or y1 - y0 < 1.0:
                continue
            regions.append(
                DetectedRegion(
                    bbox=BBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0),
                    type=RegionType.UNKNOWN,
                    confidence=score if score is not None else 0.9,
                    text=text,
                    text_confidence=score,
                )
            )
        regions.sort(key=lambda r: (r.bbox.y, r.bbox.x))
        return regions


def paddle_rec_detector(lang: str | None = None) -> RegionDetector:
    """Fused PaddleOCR detect+recognize with a baseline fallback (the ``"paddle-rec"`` name)."""
    return FallbackDetector(PaddleRecDetector(lang=lang), ConnectedComponentsDetector())


_FACTORIES: dict[str, Callable[..., RegionDetector]] = {
    "baseline": baseline_detector,
    "ml": ml_detector,
    "paddle": paddle_detector,
    "paddle-rec": paddle_rec_detector,
}


def get_detector(name: str = "baseline", *, lang: str | None = None) -> RegionDetector:
    """Resolve a detector by config name (NFR-17). Raises ``ValueError`` if unknown.

    ``lang`` (the project's source language) is forwarded to detectors that recognize text (the
    fused ``paddle-rec``); detection-only detectors ignore it.
    """
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f"unknown detector {name!r}; available: {known}") from None
    return factory(lang=lang)


def detect_file(path: Path, detector: RegionDetector) -> list[DetectedRegion]:
    """Load the image at ``path`` (read-only) and run ``detector`` on it (I-1)."""
    with Image.open(path) as image:
        array = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return detector.detect(array)
