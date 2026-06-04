"""UI layer: local web-based review editor.

The framework-free review service (:mod:`mfo.ui.review`) is always importable. The FastAPI app
(:mod:`mfo.ui.server`) lives behind the optional ``review`` extra, so importing ``mfo.ui`` never
requires FastAPI — keeping the offline core dependency-light (I-7/I-8). Import ``create_app`` from
``mfo.ui.server`` directly when serving.
"""

from __future__ import annotations

from mfo.ui.review import (
    NotFoundError,
    accept_ocr_alternative,
    create_region,
    delete_region,
    edit_translation,
    history_view,
    merge_regions,
    move_region,
    page_image_path,
    page_render_path,
    page_view,
    project_summary,
    promote_term_to_series,
    redo_edit,
    reocr_region,
    reorder_regions,
    rerender_page,
    retranslate_unit,
    review_queue,
    select_candidate,
    set_region_status,
    split_region,
    undo_edit,
    unit_view,
)

__all__ = [
    "NotFoundError",
    "project_summary",
    "page_view",
    "unit_view",
    "page_image_path",
    "edit_translation",
    "select_candidate",
    "set_region_status",
    "move_region",
    "reorder_regions",
    "split_region",
    "merge_regions",
    "create_region",
    "delete_region",
    "reocr_region",
    "accept_ocr_alternative",
    "retranslate_unit",
    "review_queue",
    "undo_edit",
    "redo_edit",
    "history_view",
    "rerender_page",
    "page_render_path",
    "promote_term_to_series",
]
