"""Vision layer: image ingest, region detection, and OCR adapters."""

from __future__ import annotations

from mfo.vision.detect import (
    BaselineConfig,
    ConnectedComponentsDetector,
    DetectedRegion,
    RegionDetector,
    baseline_detector,
    detect_file,
    get_detector,
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
from mfo.vision.preprocess import (
    PreprocessConfig,
    detect_orientation,
    estimate_skew_angle,
    preprocess_file,
    preprocess_image,
)

__all__ = [
    # detect
    "BaselineConfig",
    "ConnectedComponentsDetector",
    "DetectedRegion",
    "RegionDetector",
    "baseline_detector",
    "detect_file",
    "get_detector",
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
    # preprocess
    "PreprocessConfig",
    "detect_orientation",
    "estimate_skew_angle",
    "preprocess_file",
    "preprocess_image",
]
