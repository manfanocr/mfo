"""Storage layer: project files, SQLite, caches, and export formats."""

from __future__ import annotations

from mfo.storage.atomic import atomic_write_bytes, atomic_write_text
from mfo.storage.cache import Cache
from mfo.storage.db import SCHEMA_VERSION, Database
from mfo.storage.detect import RegionCandidate, detect_regions
from mfo.storage.hashing import content_key, sha256_bytes, sha256_file
from mfo.storage.ingest import SourceImage, import_pages
from mfo.storage.layout import DB_NAME, MANIFEST_NAME, SUBDIRS, ProjectLayout
from mfo.storage.manifest import MANIFEST_VERSION, Manifest, read_manifest, write_manifest
from mfo.storage.ocr import RecognizedSpan, ocr_regions
from mfo.storage.pipeline_state import JsonStateStore
from mfo.storage.preprocess import preprocess_pages
from mfo.storage.project import ProjectStore

__all__ = [
    # atomic
    "atomic_write_bytes",
    "atomic_write_text",
    # hashing
    "content_key",
    "sha256_bytes",
    "sha256_file",
    # cache
    "Cache",
    # layout
    "ProjectLayout",
    "MANIFEST_NAME",
    "DB_NAME",
    "SUBDIRS",
    # manifest
    "Manifest",
    "MANIFEST_VERSION",
    "read_manifest",
    "write_manifest",
    # db
    "Database",
    "SCHEMA_VERSION",
    # pipeline state
    "JsonStateStore",
    # ingest
    "import_pages",
    "SourceImage",
    # preprocess
    "preprocess_pages",
    # detect
    "detect_regions",
    "RegionCandidate",
    # ocr
    "ocr_regions",
    "RecognizedSpan",
    # project
    "ProjectStore",
]
