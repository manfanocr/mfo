"""Build and export the source → OCR → translation link graph (spec §7.6; FR-41/42/43; I-2/I-6).

Traceability is the core feature (I-2): every translated output unit must link back to its source
page, the source bounding boxes and OCR text of its regions, its full translation history (the
candidates), and the user edits applied to it (FR-42). :func:`build_mapping` assembles that graph
as a plain, JSON-serializable structure keyed on stable entity IDs (FR-41); :func:`write_mapping`
dumps it to disk (FR-43) so the mapping is preserved and reversible outside the project database
(I-6).

The output is deterministic — pages in index order, units in reading order, regions in their
stored order — so the same project and configuration yield a byte-stable mapping (NFR-26).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from mfo.core import (
    OCRSpan,
    Page,
    Region,
    TranslationCandidate,
    TranslationUnit,
    selected_text,
)
from mfo.core.geometry import BBox
from mfo.storage.atomic import atomic_write_text
from mfo.storage.edits import list_edits
from mfo.storage.project import ProjectStore

# Bumped when the exported mapping schema changes incompatibly.
MAPPING_VERSION = 1


def _bbox_payload(bbox: BBox) -> dict[str, float]:
    return {"x": bbox.x, "y": bbox.y, "width": bbox.width, "height": bbox.height}


def _region_payload(region: Region, page: Page, spans: list[OCRSpan]) -> dict[str, Any]:
    """One source region: its page, bounding box, type, order, and OCR text (FR-42)."""
    return {
        "region_id": region.id,
        "page_id": page.id,
        "page_index": page.index,
        "type": region.type.value,
        "reading_order_index": region.reading_order_index,
        "confidence": region.confidence,
        "bbox": _bbox_payload(region.bbox),
        "ocr": [
            {"span_id": span.id, "text": span.text, "confidence": span.confidence} for span in spans
        ],
    }


def _candidate_payload(candidate: TranslationCandidate) -> dict[str, Any]:
    """One entry in a unit's translation history (FR-42)."""
    return {
        "id": candidate.id,
        "kind": candidate.kind.value,
        "text": candidate.text,
        "confidence": candidate.confidence,
        "rationale": candidate.rationale,
    }


def _first_reading_order(unit: TranslationUnit, regions: dict[str, Region]) -> float:
    """Reading-order rank of a unit, taken from its first placed region (unplaced sort last)."""
    for region_id in unit.ordered_region_ids:
        region = regions.get(region_id)
        if region is not None and region.reading_order_index is not None:
            return float(region.reading_order_index)
    return math.inf


def _unit_payloads(store: ProjectStore, page: Page) -> list[dict[str, Any]]:
    """The mapping entries for every translation unit on a page, in reading order."""
    units = store.db.list(TranslationUnit, where=("page_id", page.id))
    if not units:
        return []
    regions = {region.id: region for region in store.db.list(Region, where=("page_id", page.id))}
    spans_by_region = {
        region_id: store.db.list(OCRSpan, where=("region_id", region_id)) for region_id in regions
    }

    payloads: list[dict[str, Any]] = []
    for unit in sorted(units, key=lambda u: (_first_reading_order(u, regions), u.id)):
        region_links = [
            _region_payload(regions[region_id], page, spans_by_region.get(region_id, []))
            for region_id in unit.ordered_region_ids
            if region_id in regions
        ]
        payloads.append(
            {
                "unit_id": unit.id,
                "page_id": page.id,
                "page_index": page.index,
                "style": unit.style.value if unit.style is not None else None,
                "source_text": unit.source_bundle,
                "translation": selected_text(unit),
                "selected_candidate_id": unit.selected_candidate_id,
                "regions": region_links,
                "candidates": [_candidate_payload(c) for c in unit.candidates],
                "edits": [
                    {
                        "id": record.id,
                        "before": record.before,
                        "after": record.after,
                        "action": record.action.value,
                        "editor": record.editor,
                        "timestamp": record.timestamp.isoformat(),
                    }
                    for record in list_edits(store, unit.id)
                ],
            }
        )
    return payloads


def build_mapping(store: ProjectStore) -> dict[str, Any]:
    """Assemble the full source → OCR → translation → edit link graph (FR-42)."""
    project = store.project
    units: list[dict[str, Any]] = []
    for page in store.db.list(Page, order_by="idx"):
        units.extend(_unit_payloads(store, page))
    return {
        "mapping_version": MAPPING_VERSION,
        "project": {
            "id": project.id,
            "name": project.name,
            "source_lang": project.source_lang,
            "target_lang": project.target_lang,
            "reading_direction": project.reading_direction.value,
        },
        "units": units,
    }


def write_mapping(store: ProjectStore, path: Path) -> Path:
    """Serialize the mapping to JSON at ``path`` (FR-43). Returns the path written."""
    text = json.dumps(build_mapping(store), ensure_ascii=False, indent=2)
    atomic_write_text(path, text + "\n")
    return path
