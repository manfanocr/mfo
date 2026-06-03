"""Vision layer: image ingest, region detection, and OCR adapters."""

from __future__ import annotations

from mfo.vision.detect import (
    DEFAULT_CLASS_LABELS,
    DEFAULT_OVERLAP_FRAC,
    BaselineConfig,
    ConnectedComponentsDetector,
    DetectedRegion,
    DetectionModel,
    DetectorDependencyError,
    FallbackDetector,
    MergingDetector,
    MLDetector,
    MLDetectorConfig,
    OnnxDetectionModel,
    RegionDetector,
    baseline_detector,
    classify_region,
    decode_detections,
    default_model_dir,
    detect_file,
    get_detector,
    merge_overlapping_regions,
    ml_detector,
    non_max_suppression,
)
from mfo.vision.images import (
    SUPPORTED_SUFFIXES,
    ImageError,
    is_supported,
    read_image_size,
)
from mfo.vision.ingest import (
    DiscoveredImage,
    ImportScan,
    PageOrder,
    SkippedImage,
    discover_images,
)
from mfo.vision.ocr import (
    MangaOcrEngine,
    OcrDependencyError,
    OCREngine,
    RecognizedText,
    get_ocr_engine,
    manga_ocr_engine,
    recognize_file,
)
from mfo.vision.panels import (
    Panel,
    PanelConfig,
    detect_panels,
    detect_panels_file,
)
from mfo.vision.preprocess import (
    PreprocessConfig,
    detect_orientation,
    estimate_skew_angle,
    preprocess_file,
    preprocess_image,
)

__all__ = [
    # detect
    "DEFAULT_CLASS_LABELS",
    "DEFAULT_OVERLAP_FRAC",
    "BaselineConfig",
    "ConnectedComponentsDetector",
    "DetectedRegion",
    "DetectionModel",
    "DetectorDependencyError",
    "FallbackDetector",
    "MergingDetector",
    "MLDetector",
    "MLDetectorConfig",
    "OnnxDetectionModel",
    "RegionDetector",
    "baseline_detector",
    "classify_region",
    "decode_detections",
    "default_model_dir",
    "detect_file",
    "get_detector",
    "merge_overlapping_regions",
    "ml_detector",
    "non_max_suppression",
    # images
    "SUPPORTED_SUFFIXES",
    "ImageError",
    "is_supported",
    "read_image_size",
    # ingest
    "DiscoveredImage",
    "ImportScan",
    "PageOrder",
    "SkippedImage",
    "discover_images",
    # ocr
    "MangaOcrEngine",
    "OCREngine",
    "OcrDependencyError",
    "RecognizedText",
    "get_ocr_engine",
    "manga_ocr_engine",
    "recognize_file",
    # panels
    "Panel",
    "PanelConfig",
    "detect_panels",
    "detect_panels_file",
    # preprocess
    "PreprocessConfig",
    "detect_orientation",
    "estimate_skew_angle",
    "preprocess_file",
    "preprocess_image",
]
