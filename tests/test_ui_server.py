"""Tests for the review HTTP API (spec §13.2; FR-37/42/49; I-3).

Exercises the FastAPI shell end to end with the in-process test client. Skipped entirely when the
optional ``review`` extra (FastAPI) is not installed, so the offline core stays unaffected.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from mfo.core import OCRSpan, Page, Project, Region, TranslationUnit  # noqa: E402
from mfo.core.enums import RegionType  # noqa: E402
from mfo.core.geometry import BBox  # noqa: E402
from mfo.storage import ProjectStore, import_pages, list_edits, translate_units  # noqa: E402
from mfo.ui.server import create_app  # noqa: E402
from mfo.vision.ingest import discover_images  # noqa: E402


@dataclass(frozen=True)
class _Result:
    text: str
    confidence: float | None = None


def _echo(source: str, context: dict[str, object]) -> _Result:
    return _Result(text=f"EN[{source}]", confidence=0.8)


def _seed(root: Path, source: Path) -> ProjectStore:
    source.mkdir()
    arr = np.full((100, 100, 3), 255, dtype=np.uint8)
    arr[15:40, 15:70] = 0
    Image.fromarray(arr, mode="RGB").save(source / "p1.png")

    store = ProjectStore.create(
        root,
        Project(name="vol", source_lang="ja", target_lang="en"),
        check_same_thread=False,  # the TestClient runs handlers on worker threads
    )
    import_pages(store, discover_images(source).images)
    page = store.db.list(Page)[0]
    region = Region(
        page_id=page.id,
        bbox=BBox(x=15, y=15, width=55, height=25),
        reading_order_index=0,
        type=RegionType.BUBBLE,
    )
    store.db.save(region)
    store.db.save(OCRSpan(region_id=region.id, text="こんにちは", confidence=0.9))
    store.db.save(TranslationUnit(page_id=page.id, ordered_region_ids=[region.id]))
    translate_units(store, translate=_echo, signature="fake@1", target_lang="en")
    return store


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, ProjectStore]]:
    with _seed(tmp_path / "proj", tmp_path / "src") as store:
        yield TestClient(create_app(store)), store


def test_get_project_lists_pages(client: tuple[TestClient, ProjectStore]) -> None:
    api, _ = client
    response = api.get("/api/project")
    assert response.status_code == 200
    body = response.json()
    assert body["project"]["name"] == "vol"
    assert len(body["pages"]) == 1


def test_get_page_serves_regions_and_units(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    page = store.db.list(Page)[0]
    body = api.get(f"/api/pages/{page.id}").json()
    assert body["regions"][0]["ocr"][0]["text"] == "こんにちは"
    assert body["units"][0]["translation"] == "EN[こんにちは]"


def test_get_page_image_returns_the_source(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    page = store.db.list(Page)[0]
    response = api.get(f"/api/pages/{page.id}/image")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_unknown_page_is_404(client: tuple[TestClient, ProjectStore]) -> None:
    api, _ = client
    assert api.get("/api/pages/pg_missing").status_code == 404


def test_put_translation_edits_and_records(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    unit = store.db.list(TranslationUnit)[0]
    response = api.put(f"/api/units/{unit.id}/translation", json={"text": "Hello!"})
    assert response.status_code == 200
    assert response.json()["translation"] == "Hello!"
    assert len(list_edits(store, unit.id)) == 1  # persisted as a record (FR-42)


def test_post_select_reverts_candidate(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    unit = store.db.list(TranslationUnit)[0]
    raw_id = unit.candidates[0].id
    api.put(f"/api/units/{unit.id}/translation", json={"text": "Manual"})

    response = api.post(f"/api/units/{unit.id}/select", json={"candidate_id": raw_id})
    assert response.status_code == 200
    assert response.json()["translation"] == "EN[こんにちは]"


def test_select_unknown_candidate_is_404(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    unit = store.db.list(TranslationUnit)[0]
    response = api.post(f"/api/units/{unit.id}/select", json={"candidate_id": "cand_missing"})
    assert response.status_code == 404


# -- the bundled editor SPA (§13.1-13.5) --------------------------------------------------


def test_root_serves_the_editor_page(client: tuple[TestClient, ProjectStore]) -> None:
    api, _ = client
    response = api.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "mfo review" in response.text


def test_static_assets_are_served(client: tuple[TestClient, ProjectStore]) -> None:
    api, _ = client
    for asset, marker in (("app.js", "/api/project"), ("app.css", "--bg")):
        response = api.get(f"/static/{asset}")
        assert response.status_code == 200, asset
        assert marker in response.text
