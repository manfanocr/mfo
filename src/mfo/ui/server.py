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
    edit_translation,
    page_image_path,
    page_view,
    project_summary,
    select_candidate,
    unit_view,
)

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
    raise ModuleNotFoundError(
        "The review editor needs FastAPI. Install it with:  pip install 'mfo[review]'"
    ) from exc

STATIC_DIR = Path(__file__).parent / "static"


class TranslationEdit(BaseModel):
    """Body for editing a unit's translation in place (FR-37)."""

    text: str
    editor: str = "user"


class CandidateSelection(BaseModel):
    """Body for re-selecting one of a unit's existing candidates (FR-49)."""

    candidate_id: str
    editor: str = "user"


def create_app(store: ProjectStore) -> FastAPI:
    """Build the review API bound to an open project ``store``.

    Read routes serve the page-editor data (§13.2); mutation routes apply edits that win over
    automation (I-3) and append edit records (FR-42). The caller owns the store's lifecycle.
    """
    app = FastAPI(title="mfo review", version="1")

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/api/project")
    def get_project() -> dict[str, Any]:
        return project_summary(store)

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

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # The SPA's assets (CSS/JS); mounted last so it never shadows the API routes above.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
