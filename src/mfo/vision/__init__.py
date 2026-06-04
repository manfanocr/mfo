"""Vision layer: image ingest, region detection, and OCR adapters."""

from __future__ import annotations

from mfo.core.assets import default_model_dir
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
    ARCHIVE_SUFFIXES,
    DiscoveredImage,
    ImportScan,
    PageOrder,
    SkippedImage,
    discover_images,
    extract_archive,
    is_archive,
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
from mfo.vision.sfx import (
    HeuristicSfxClassifier,
    SfxClassifier,
    SfxFeatures,
    classify_region_type,
    get_sfx_classifier,
    heuristic_sfx_classifier,
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
    "ARCHIVE_SUFFIXES",
    "DiscoveredImage",
    "ImportScan",
    "PageOrder",
    "SkippedImage",
    "discover_images",
    "extract_archive",
    "is_archive",
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
    # sfx classification
    "HeuristicSfxClassifier",
    "SfxClassifier",
    "SfxFeatures",
    "classify_region_type",
    "get_sfx_classifier",
    "heuristic_sfx_classifier",
]
