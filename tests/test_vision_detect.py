"""Tests for the region detection adapter + connected-components baseline (§10.3; FR-10/11).

Also covers the optional ML detector adapter (batch 2.2; FR-11, FR-14): class→type mapping, NMS,
letterbox/decode geometry, threshold/order, lazy model resolution, and the baseline fallback.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mfo.core.enums import RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.vision.detect import (
    DEFAULT_CLASS_LABELS,
    ConnectedComponentsDetector,
    DetectedRegion,
    DetectorDependencyError,
    FallbackDetector,
    MLDetector,
    MLDetectorConfig,
    OnnxDetectionModel,
    PaddleDetector,
    _paddle_boxes,
    classify_region,
    decode_detections,
    default_model_dir,
    detect_file,
    get_detector,
    ml_detector,
    non_max_suppression,
    paddle_detector,
)

_ONNXRUNTIME_INSTALLED = importlib.util.find_spec("onnxruntime") is not None
_PADDLEOCR_INSTALLED = importlib.util.find_spec("paddleocr") is not None


def _page_with_blocks() -> np.ndarray:
    """A white page with three solid black blocks of distinct shapes (bubble/wide/tall)."""
    img = np.full((300, 200, 3), 255, dtype=np.uint8)
    img[20:40, 20:60] = 0  # 40x20 compact block  → bubble
    img[80:90, 20:100] = 0  # 80x10 wide block     → narration
    img[120:160, 20:30] = 0  # 10x40 tall strip    → side text
    return img


def _bbox_near(
    region: DetectedRegion, x: float, y: float, w: float, h: float, tol: int = 4
) -> bool:
    b = region.bbox
    return (
        abs(b.x - x) <= tol
        and abs(b.y - y) <= tol
        and abs(b.width - w) <= tol
        and abs(b.height - h) <= tol
    )


def test_baseline_detects_blocks_in_reading_order() -> None:
    regions = ConnectedComponentsDetector().detect(_page_with_blocks())
    assert len(regions) == 3
    # Sorted top-to-bottom.
    assert [r.type for r in regions] == [
        RegionType.BUBBLE,
        RegionType.NARRATION,
        RegionType.SIDE_TEXT,
    ]
    assert _bbox_near(regions[0], 20, 20, 40, 20)
    assert _bbox_near(regions[1], 20, 80, 80, 10)
    assert _bbox_near(regions[2], 20, 120, 10, 40)


def test_confidence_is_bounded() -> None:
    for region in ConnectedComponentsDetector().detect(_page_with_blocks()):
        assert 0.0 <= region.confidence <= 1.0


def test_blank_page_yields_no_regions() -> None:
    blank = np.full((120, 120, 3), 255, dtype=np.uint8)
    assert ConnectedComponentsDetector().detect(blank) == []


def test_tiny_speck_is_filtered_out() -> None:
    img = np.full((300, 300, 3), 255, dtype=np.uint8)
    img[10:12, 10:12] = 0  # 2x2 speck — below min area fraction
    assert ConnectedComponentsDetector().detect(img) == []


def test_oversized_blob_is_kept_but_auto_ignored() -> None:
    # A panel-sized block (>suspect_area_frac of the page) must not pass as a confident bubble: it
    # is kept (not dropped) but auto-marked IGNORE with a capped score (bug: whole frames as
    # bubbles; I-4) so OCR/translate/render and the review queue skip it.
    img = np.full((300, 300, 3), 255, dtype=np.uint8)
    img[30:180, 30:180] = 0  # 150x150 = 25% of the page → suspicious by area
    [region] = ConnectedComponentsDetector().detect(img)
    assert region.status is RegionStatus.IGNORE
    assert region.confidence <= 0.3


def test_wide_frame_is_ignored() -> None:
    # A band spanning most of the page width is a panel/frame even when its area is modest: the
    # wide_frac heuristic catches it and auto-ignores it (item 11).
    img = np.full((300, 300, 3), 255, dtype=np.uint8)
    img[100:120, 10:290] = 0  # 280px wide (~93% of the page) but only ~6% of its area
    [region] = ConnectedComponentsDetector().detect(img)
    assert region.status is RegionStatus.IGNORE


def test_normal_block_is_auto() -> None:
    # A small, well-formed block stays trusted (status auto), so flagging is targeted not blanket.
    img = np.full((300, 300, 3), 255, dtype=np.uint8)
    img[20:40, 20:60] = 0  # 40x20 ≈ 0.9% of the page
    [region] = ConnectedComponentsDetector().detect(img)
    assert region.status is RegionStatus.AUTO


def test_get_detector_returns_baseline_and_rejects_unknown() -> None:
    assert isinstance(get_detector("baseline"), ConnectedComponentsDetector)
    with pytest.raises(ValueError, match="unknown detector"):
        get_detector("does-not-exist")


def test_detect_file_reads_image_and_detects(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    Image.fromarray(_page_with_blocks(), mode="RGB").save(path)
    regions = detect_file(path, ConnectedComponentsDetector())
    assert len(regions) == 3


# -- ML detector adapter (batch 2.2; FR-11, FR-14; NFR-22) --------------------------------------


class _FakeModel:
    """A fake DetectionModel returning canned boxes, so adapter logic runs without a runtime."""

    def __init__(self, regions: list[DetectedRegion]) -> None:
        self._regions = regions
        self.calls = 0

    def infer(self, image: np.ndarray) -> list[DetectedRegion]:
        self.calls += 1
        return list(self._regions)


def _region(
    x: float, y: float, w: float, h: float, conf: float, rtype: RegionType
) -> DetectedRegion:
    return DetectedRegion(bbox=BBox(x=x, y=y, width=w, height=h), type=rtype, confidence=conf)


def test_classify_region_maps_indices_and_defaults_unknown() -> None:
    assert classify_region(0) == RegionType.BUBBLE
    assert classify_region(2) == RegionType.SFX
    assert classify_region(len(DEFAULT_CLASS_LABELS)) == RegionType.UNKNOWN
    assert classify_region(-1) == RegionType.UNKNOWN


def test_non_max_suppression_drops_overlaps_keeping_highest() -> None:
    strong = _region(0, 0, 100, 100, 0.9, RegionType.BUBBLE)
    weak_overlap = _region(5, 5, 100, 100, 0.5, RegionType.BUBBLE)  # ~0.8 IoU with strong
    far = _region(500, 500, 50, 50, 0.6, RegionType.BUBBLE)
    kept = non_max_suppression([weak_overlap, strong, far], iou_threshold=0.45)
    assert strong in kept and far in kept
    assert weak_overlap not in kept


def test_decode_detections_unletterboxes_and_classifies() -> None:
    # One box in model space; with scale=0.5, pad=(10, 20) it maps back to source pixels.
    rows = np.array([[30.0, 40.0, 80.0, 140.0, 0.7, 1.0]], dtype=np.float32)
    [region] = decode_detections(rows, scale=0.5, pad_x=10.0, pad_y=20.0)
    assert region.type == RegionType.NARRATION
    assert region.confidence == 0.7
    assert region.bbox == BBox(x=40.0, y=40.0, width=100.0, height=200.0)


def test_ml_detector_thresholds_nms_and_orders() -> None:
    model = _FakeModel(
        [
            _region(10, 200, 40, 40, 0.9, RegionType.BUBBLE),  # lower on page
            _region(10, 10, 40, 40, 0.8, RegionType.NARRATION),  # higher on page
            _region(12, 12, 40, 40, 0.6, RegionType.NARRATION),  # overlaps the previous → NMS
            _region(10, 100, 40, 40, 0.1, RegionType.SFX),  # below threshold → dropped
        ]
    )
    detector = MLDetector(MLDetectorConfig(score_threshold=0.3, nms_iou=0.45), model=model)
    regions = detector.detect(np.zeros((400, 400, 3), dtype=np.uint8))
    assert [r.type for r in regions] == [RegionType.NARRATION, RegionType.BUBBLE]
    assert [r.bbox.y for r in regions] == [10.0, 200.0]  # top-to-bottom order


def test_ml_detector_clamps_boxes_to_the_page() -> None:
    model = _FakeModel([_region(380, 380, 100, 100, 0.9, RegionType.BUBBLE)])
    detector = MLDetector(MLDetectorConfig(), model=model)
    [region] = detector.detect(np.zeros((400, 400, 3), dtype=np.uint8))
    assert region.bbox.right <= 400 and region.bbox.bottom <= 400


def test_fallback_uses_primary_when_available() -> None:
    primary = MLDetector(model=_FakeModel([_region(10, 10, 40, 40, 0.9, RegionType.BUBBLE)]))
    detector = FallbackDetector(primary, ConnectedComponentsDetector())
    regions = detector.detect(np.zeros((100, 100, 3), dtype=np.uint8))
    assert len(regions) == 1
    # Signature is a stable composite regardless of which backend wins (NFR-8).
    assert detector.name == "ml-detector+fallback"
    assert detector.version == "1+1"


def test_fallback_drops_to_baseline_when_model_unavailable() -> None:
    class _MissingModel:
        def infer(self, image: np.ndarray) -> list[DetectedRegion]:
            raise DetectorDependencyError("no model")

    detector = FallbackDetector(MLDetector(model=_MissingModel()), ConnectedComponentsDetector())
    regions = detector.detect(_page_with_blocks())
    assert len(regions) == 3  # baseline ran instead of hard-failing


def test_get_detector_resolves_ml_to_a_fallback_detector() -> None:
    detector = get_detector("ml")
    assert isinstance(detector, FallbackDetector)
    assert isinstance(ml_detector(), FallbackDetector)


def test_get_detector_resolves_paddle_to_a_fallback_detector() -> None:
    assert isinstance(get_detector("paddle"), FallbackDetector)
    assert isinstance(paddle_detector(), FallbackDetector)


def _break_paddle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the paddle text detector to be unavailable, whether or not paddleocr is installed.

    If paddleocr is present, its ``TextDetection`` constructor is monkeypatched to raise (standing
    in for a missing ``paddlepaddle`` backend); if absent, the import fails on its own.
    """
    if _PADDLEOCR_INSTALLED:
        import paddleocr

        def _boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("paddlepaddle backend is not installed")

        monkeypatch.setattr(paddleocr, "TextDetection", _boom)


def test_paddle_boxes_flattens_3x_detection_results() -> None:
    # TextDetection.predict() returns one dict-like result per image with a dt_polys list; both
    # NumPy point arrays and plain nested lists are accepted, degenerate/malformed polys dropped.
    raw = [
        {
            "dt_polys": [
                np.array([[0, 0], [9, 0], [9, 9], [0, 9]]),  # NumPy quad
                [[1, 1], [2, 1]],  # only two points → dropped
            ]
        }
    ]
    boxes = _paddle_boxes(raw)
    assert boxes == [[(0.0, 0.0), (9.0, 0.0), (9.0, 9.0), (0.0, 9.0)]]
    assert _paddle_boxes([{"rec_texts": ["x"]}]) == []  # rec-only result → no boxes
    assert _paddle_boxes(None) == []
    assert _paddle_boxes([None]) == []


def test_paddle_detector_reports_missing_dependency_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_paddle(monkeypatch)
    with pytest.raises(DetectorDependencyError, match="pip install"):
        PaddleDetector().detect(np.zeros((10, 10, 3), dtype=np.uint8))


def test_paddle_falls_back_to_baseline_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The "paddle" config name wraps PaddleDetector in a baseline fallback, so a missing paddle
    # backend never hard-fails detection (mirrors the ml fallback). This is the exact path hit by
    # `mfo detect --detector paddle` when paddlepaddle is not installed.
    _break_paddle(monkeypatch)
    regions = paddle_detector().detect(_page_with_blocks())
    assert len(regions) == 3


def test_default_model_dir_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MFO_MODEL_DIR", "/tmp/mfo-models")
    assert default_model_dir() == Path("/tmp/mfo-models")


def test_ensure_model_file_errors_when_absent_and_no_url(tmp_path: Path) -> None:
    model = OnnxDetectionModel(MLDetectorConfig(model_dir=tmp_path, model_url=""))
    with pytest.raises(DetectorDependencyError, match="model not found"):
        model.ensure_model_file()


def test_ensure_model_file_downloads_from_url(tmp_path: Path) -> None:
    source = tmp_path / "weights.onnx"
    source.write_bytes(b"fake-onnx")
    cache = tmp_path / "cache"
    model = OnnxDetectionModel(
        MLDetectorConfig(model_dir=cache, model_filename="m.onnx", model_url=source.as_uri())
    )
    path = model.ensure_model_file()
    assert path == cache / "m.onnx"
    assert path.read_bytes() == b"fake-onnx"
    assert not (cache / "m.onnx.part").exists()  # temp cleaned up after atomic rename


@pytest.mark.skipif(
    _ONNXRUNTIME_INSTALLED, reason="onnxruntime is installed; can't test its absence"
)
def test_onnx_model_reports_missing_dependency_clearly(tmp_path: Path) -> None:
    model = OnnxDetectionModel(MLDetectorConfig(model_dir=tmp_path))
    with pytest.raises(DetectorDependencyError, match="pip install"):
        model.infer(np.zeros((10, 10, 3), dtype=np.uint8))
