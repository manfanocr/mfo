"""FastAPI app exposing the review backend over HTTP and serving the editor SPA (spec §13).

A thin shell over :mod:`mfo.ui.review`: every API route resolves to a framework-free service
function, so the routing here stays declarative and the logic stays testable without a server. The
app reads project state (pages, regions, OCR, translations, confidence, edit history) and applies
the two review edits — translation-in-place and candidate re-selection — each persisted as an
:class:`~mfo.core.models.EditRecord`. Source images are served read-only (I-1). The bundled
single-page editor under ``static/`` is served at ``/`` and consumes that same API (§13.1-13.5).

FastAPI is an optional dependency (the ``review`` extra); importing this module without it raises a
clear, actionable error rather than a bare ``ImportError`` so the offline core stays unaffected
(I-7/I-8). :func:`serve` launches the local app behind ``mfo review``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mfo.storage.project import ProjectStore
from mfo.ui.review import (
    NotFoundError,
    create_region,
    delete_region,
    edit_translation,
    merge_regions,
    move_region,
    page_image_path,
    page_render_path,
    page_view,
    project_summary,
    reorder_regions,
    rerender_page,
    review_queue,
    select_candidate,
    set_region_status,
    split_region,
    unit_view,
)

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    from starlette.types import Scope
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
    raise ModuleNotFoundError(
        "The review editor needs FastAPI. Install it with:  pip install 'mfo[review]'"
    ) from exc

STATIC_DIR = Path(__file__).parent / "static"

# The bundled SPA changes between releases; ``no-cache`` makes the browser revalidate every load
# (cheap 304s via ETag) so an updated app.js/css is never masked by a stale cached copy.
_NO_CACHE = "no-cache"


class _NoCacheStaticFiles(StaticFiles):
    """Serve the SPA assets but force revalidation so updated bundles are always picked up."""

    async def get_response(self, path: str, scope: Scope) -> Any:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = _NO_CACHE
        return response


class TranslationEdit(BaseModel):
    """Body for editing a unit's translation in place (FR-37)."""

    text: str
    editor: str = "user"


class CandidateSelection(BaseModel):
    """Body for re-selecting one of a unit's existing candidates (FR-49)."""

    candidate_id: str
    editor: str = "user"


class RegionStatusEdit(BaseModel):
    """Body for flagging a region's review status (FR-40)."""

    status: str


class RegionBBoxEdit(BaseModel):
    """Body for repositioning/resizing a region's bounding box (FR-38)."""

    x: float
    y: float
    width: float
    height: float


class RegionOrder(BaseModel):
    """Body for a manual reading-order correction on a page (FR-20)."""

    ordered_region_ids: list[str]


class RegionSplit(BaseModel):
    """Body for splitting a region into two adjacent regions (FR-39)."""

    orientation: str = "horizontal"
    ratio: float = 0.5


class RegionMerge(BaseModel):
    """Body for merging several regions on a page into one (FR-39)."""

    region_ids: list[str]


class RegionCreate(BaseModel):
    """Body for adding a user-drawn region, then OCR-ing and translating it (FR-38; §13.3)."""

    x: float
    y: float
    width: float
    height: float
    type: str = "bubble"


# The OCR/translate engines a created region needs. Wired lazily from the project config so this
# module never pulls in the heavy vision/language stacks at import (I-7/I-8); split out as a seam so
# the create-region route can be tested with fakes instead of real engines.
RegionEngines = tuple[
    Any, Any, str, Any, Any
]  # (recognize, translate, target_lang, style, glossary)


def _region_engines(store: ProjectStore) -> RegionEngines:
    from mfo.cli.stages import load_glossary
    from mfo.core.enums import TranslationStyle
    from mfo.language.translate import TranslationRequest, get_translator
    from mfo.vision.ocr import get_ocr_engine, recognize_file

    project = store.project
    ocr_cfg = project.config.get("ocr", {})
    translate_cfg = project.config.get("translate", {})
    ocr_engine = get_ocr_engine(ocr_cfg.get("engine", "manga-ocr"))
    translator = get_translator(translate_cfg.get("translator", "argos"))
    style = TranslationStyle(translate_cfg.get("style", TranslationStyle.BALANCED.value))
    source_lang, target_lang = project.source_lang, project.target_lang

    def recognize(path: Any, bbox: Any) -> Any:
        return recognize_file(path, bbox, ocr_engine)

    def translate(source: str, context: dict[str, Any]) -> Any:
        return translator.translate(
            TranslationRequest(
                source=source,
                source_lang=source_lang,
                target_lang=target_lang,
                context=context,
                style=style,
            )
        )

    return recognize, translate, target_lang, style, load_glossary(store)


def create_app(store: ProjectStore) -> FastAPI:
    """Build the review API bound to an open project ``store``.

    Read routes serve the page-editor data (§13.2); mutation routes apply edits that win over
    automation (I-3) and append edit records (FR-42). The caller owns the store's lifecycle.
    """
    app = FastAPI(title="mfo review", version="1")

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
        # Rejected region ops (bad status, ratio, permutation, …) are client errors, not crashes.
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/project")
    def get_project() -> dict[str, Any]:
        return project_summary(store)

    @app.get("/api/review-queue")
    def get_review_queue() -> dict[str, Any]:
        return review_queue(store)

    @app.get("/api/pages/{page_id}")
    def get_page(page_id: str) -> dict[str, Any]:
        return page_view(store, page_id)

    @app.get("/api/pages/{page_id}/image")
    def get_page_image(page_id: str) -> FileResponse:
        return FileResponse(page_image_path(store, page_id))

    @app.get("/api/units/{unit_id}")
    def get_unit(unit_id: str) -> dict[str, Any]:
        return unit_view(store, unit_id)

    @app.put("/api/units/{unit_id}/translation")
    def put_translation(unit_id: str, body: TranslationEdit) -> dict[str, Any]:
        return edit_translation(store, unit_id, body.text, editor=body.editor)

    @app.post("/api/units/{unit_id}/select")
    def post_select(unit_id: str, body: CandidateSelection) -> dict[str, Any]:
        return select_candidate(store, unit_id, body.candidate_id, editor=body.editor)

    # -- region operations (§13.3/13.4; FR-20/38/39/40); each returns the refreshed page view --

    @app.put("/api/regions/{region_id}/status")
    def put_region_status(region_id: str, body: RegionStatusEdit) -> dict[str, Any]:
        return set_region_status(store, region_id, body.status)

    @app.put("/api/regions/{region_id}/bbox")
    def put_region_bbox(region_id: str, body: RegionBBoxEdit) -> dict[str, Any]:
        return move_region(
            store, region_id, x=body.x, y=body.y, width=body.width, height=body.height
        )

    @app.post("/api/regions/{region_id}/split")
    def post_region_split(region_id: str, body: RegionSplit) -> dict[str, Any]:
        return split_region(store, region_id, orientation=body.orientation, ratio=body.ratio)

    @app.post("/api/regions/merge")
    def post_regions_merge(body: RegionMerge) -> dict[str, Any]:
        return merge_regions(store, body.region_ids)

    @app.delete("/api/regions/{region_id}")
    def delete_region_route(region_id: str) -> dict[str, Any]:
        return delete_region(store, region_id)

    @app.post("/api/pages/{page_id}/regions")
    def post_create_region(page_id: str, body: RegionCreate) -> dict[str, Any]:
        from mfo.core.enums import RegionType
        from mfo.language.translate import TranslatorDependencyError
        from mfo.vision.ocr import OcrDependencyError

        try:
            region_type = RegionType(body.type)
        except ValueError:
            region_type = RegionType.BUBBLE
        recognize, translate, target_lang, style, glossary = _region_engines(store)
        try:
            return create_region(
                store,
                page_id,
                x=body.x,
                y=body.y,
                width=body.width,
                height=body.height,
                recognize=recognize,
                translate=translate,
                target_lang=target_lang,
                region_type=region_type,
                style=style,
                glossary=glossary,
            )
        except (OcrDependencyError, TranslatorDependencyError) as exc:
            # The offline core works without these engines; surface a clear, actionable 503.
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.put("/api/pages/{page_id}/order")
    def put_page_order(page_id: str, body: RegionOrder) -> dict[str, Any]:
        return reorder_regions(store, page_id, body.ordered_region_ids)

    # -- re-render preview (§13.3) --

    @app.post("/api/pages/{page_id}/render")
    def post_page_render(page_id: str) -> dict[str, Any]:
        return rerender_page(store, page_id)

    @app.get("/api/pages/{page_id}/render")
    def get_page_render(page_id: str) -> FileResponse:
        return FileResponse(page_render_path(store, page_id))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": _NO_CACHE})

    # The SPA's assets (CSS/JS); mounted last so it never shadows the API routes above.
    app.mount("/static", _NoCacheStaticFiles(directory=STATIC_DIR), name="static")

    return app


def serve(
    store: ProjectStore,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the review editor locally with uvicorn (blocking) — the body of ``mfo review``.

    Binds to localhost by default so the editor stays a local-first, offline tool (I-7/I-8); the
    caller owns the store's lifecycle. uvicorn ships with the ``review`` extra.
    """
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
        raise ModuleNotFoundError(
            "The review editor needs uvicorn. Install it with:  pip install 'mfo[review]'"
        ) from exc

    uvicorn.run(create_app(store), host=host, port=port, log_level="warning")
