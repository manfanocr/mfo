"""Tests for the review HTTP API (spec §13.2; FR-37/42/49; I-3).

Exercises the FastAPI shell end to end with the in-process test client. Skipped entirely when the
optional ``review`` extra (FastAPI) is not installed, so the offline core stays unaffected.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

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
    # The SPA's page list keys off ``page_id`` (not ``id``); locking it here guards the contract.
    assert "page_id" in body["pages"][0]


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


# -- region operations (§13.3/13.4; FR-20/38/39/40) ---------------------------------------


def test_put_region_status(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    response = api.put(f"/api/regions/{region.id}/status", json={"status": "correct"})
    assert response.status_code == 200
    assert response.json()["regions"][0]["status"] == "correct"


def test_put_region_status_bad_value_is_400(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    response = api.put(f"/api/regions/{region.id}/status", json={"status": "nope"})
    assert response.status_code == 400


def test_put_region_bbox(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    body = {"x": 1, "y": 2, "width": 30, "height": 40}
    response = api.put(f"/api/regions/{region.id}/bbox", json=body)
    assert response.status_code == 200
    assert response.json()["regions"][0]["bbox"] == {"x": 1, "y": 2, "width": 30, "height": 40}


def test_split_then_merge_round_trips(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    page_id = region.page_id

    split = api.post(f"/api/regions/{region.id}/split", json={"ratio": 0.5})
    assert split.status_code == 200
    ids = [r["region_id"] for r in split.json()["regions"]]
    assert len(ids) == 2

    merged = api.post("/api/regions/merge", json={"region_ids": ids})
    assert merged.status_code == 200
    assert len(merged.json()["regions"]) == 1
    assert merged.json()["page_id"] == page_id


def test_delete_region_route(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    response = api.delete(f"/api/regions/{region.id}")
    assert response.status_code == 200
    assert response.json()["regions"] == []
    assert store.db.get(Region, region.id) is None


def test_create_region_route_runs_ocr_and_translate(
    client: tuple[TestClient, ProjectStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    page = store.db.list(Page)[0]

    # Inject fake engines so the route is exercised without the heavy OCR/MT stacks.
    def fake_engines(_store: ProjectStore) -> tuple[object, object, str, object, tuple[()]]:
        def recognize(path: Path, bbox: BBox) -> SimpleNamespace:
            return SimpleNamespace(text="やあ", confidence=None, alternatives=[])

        return recognize, _echo, "en", "balanced", ()

    monkeypatch.setattr("mfo.ui.server._region_engines", fake_engines)

    body = {"x": 5, "y": 5, "width": 30, "height": 20, "type": "bubble"}
    response = api.post(f"/api/pages/{page.id}/regions", json=body)
    assert response.status_code == 200
    assert len(response.json()["regions"]) == 2  # original + the created one


def test_create_region_route_reports_engine_unavailable(
    client: tuple[TestClient, ProjectStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    page = store.db.list(Page)[0]

    def engines_with_failing_ocr(
        _store: ProjectStore,
    ) -> tuple[object, object, str, object, tuple[()]]:
        from mfo.vision.ocr import OcrDependencyError

        def recognize(path: Path, bbox: BBox) -> _Result:
            raise OcrDependencyError("manga-ocr is not installed")

        return recognize, _echo, "en", "balanced", ()

    monkeypatch.setattr("mfo.ui.server._region_engines", engines_with_failing_ocr)
    body = {"x": 5, "y": 5, "width": 30, "height": 20}
    response = api.post(f"/api/pages/{page.id}/regions", json=body)
    assert response.status_code == 503
    assert "manga-ocr" in response.json()["detail"]


def test_reocr_route_runs_ocr(
    client: tuple[TestClient, ProjectStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    region = store.db.list(Region)[0]

    def fake_engines(_store: ProjectStore) -> tuple[object, object, str, object, tuple[()]]:
        def recognize(path: Path, bbox: BBox) -> SimpleNamespace:
            return SimpleNamespace(text="ZZ", confidence=None, alternatives=[])

        return recognize, _echo, "en", "balanced", ()

    monkeypatch.setattr("mfo.ui.server._region_engines", fake_engines)
    response = api.post(f"/api/regions/{region.id}/ocr")
    assert response.status_code == 200
    assert response.json()["regions"][0]["ocr"][0]["text"] == "ZZ"


def test_translate_route_runs(
    client: tuple[TestClient, ProjectStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mfo.core.enums import TranslationStyle

    api, store = client
    unit = store.db.list(TranslationUnit)[0]

    def fake_engines(_store: ProjectStore) -> tuple[object, object, str, object, tuple[()]]:
        def recognize(path: Path, bbox: BBox) -> SimpleNamespace:
            return SimpleNamespace(text="x", confidence=None, alternatives=[])

        return recognize, _echo, "en", TranslationStyle.BALANCED, ()

    monkeypatch.setattr("mfo.ui.server._region_engines", fake_engines)
    response = api.post(f"/api/units/{unit.id}/translate")
    assert response.status_code == 200
    assert response.json()["translation"].startswith("EN[")  # re-translated from current OCR


def test_series_promote_route(client: tuple[TestClient, ProjectStore], tmp_path: Path) -> None:
    from mfo.cli.stages import link_series_glossary, save_glossary
    from mfo.core import GlossaryEntry
    from mfo.storage.series import load_series_glossary

    api, store = client
    store_path = tmp_path / "series.json"
    link_series_glossary(store, store_path)
    save_glossary(store, (GlossaryEntry(source="鬼", target="oni"),))

    response = api.post("/api/glossary/series/promote", json={"source": "鬼"})
    assert response.status_code == 200
    assert response.json() == {"source": "鬼", "target": "oni"}
    # The term is now in the shared store, available to other volumes (SG-2).
    assert load_series_glossary(store_path).entries[0].source == "鬼"


def test_series_promote_without_link_is_400(
    client: tuple[TestClient, ProjectStore],
) -> None:
    api, _ = client
    response = api.post("/api/glossary/series/promote", json={"source": "鬼"})
    assert response.status_code == 400


def test_reocr_route_reports_engine_unavailable(
    client: tuple[TestClient, ProjectStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, store = client
    region = store.db.list(Region)[0]

    def engines(_store: ProjectStore) -> tuple[object, object, str, object, tuple[()]]:
        from mfo.vision.ocr import OcrDependencyError

        def recognize(path: Path, bbox: BBox) -> _Result:
            raise OcrDependencyError("manga-ocr is not installed")

        return recognize, _echo, "en", "balanced", ()

    monkeypatch.setattr("mfo.ui.server._region_engines", engines)
    response = api.post(f"/api/regions/{region.id}/ocr")
    assert response.status_code == 503
    assert "manga-ocr" in response.json()["detail"]


def test_undo_redo_routes_restore_a_region(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    api.delete(f"/api/regions/{region.id}")
    assert store.db.get(Region, region.id) is None

    undo = api.post("/api/undo", json={})
    assert undo.status_code == 200
    assert undo.json()["affected_page_id"] == region.page_id
    assert store.db.get(Region, region.id) is not None  # the delete was undone

    redo = api.post("/api/redo", json={})
    assert redo.status_code == 200
    assert store.db.get(Region, region.id) is None  # and re-applied


def test_history_route_lists_entries(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    api.delete(f"/api/regions/{region.id}")

    body = api.get("/api/history").json()
    assert body["scope"] == "global"
    assert len(body["entries"]) == 1
    assert body["entries"][0]["action"] == "delete_region"
    assert body["can_undo"] is True and body["can_redo"] is False


def test_put_page_order(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    region = store.db.list(Region)[0]
    response = api.put(
        f"/api/pages/{region.page_id}/order", json={"ordered_region_ids": [region.id]}
    )
    assert response.status_code == 200
    assert response.json()["regions"][0]["reading_order_index"] == 0


def test_get_review_queue(client: tuple[TestClient, ProjectStore]) -> None:
    api, _ = client
    response = api.get("/api/review-queue")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 1
    # AI uncertainty fields ride on every entry (absent AI layer → not flagged) (FR-30).
    assert entries[0]["ai_flagged"] is False
    assert entries[0]["ai_rationale"] is None


def test_render_endpoints(client: tuple[TestClient, ProjectStore]) -> None:
    api, store = client
    page = store.db.list(Page)[0]

    # Before rendering, the render image is not available yet.
    assert api.get(f"/api/pages/{page.id}/render").status_code == 404

    rendered = api.post(f"/api/pages/{page.id}/render")
    assert rendered.status_code == 200
    assert rendered.json()["rendered"] is True

    image = api.get(f"/api/pages/{page.id}/render")
    assert image.status_code == 200
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"


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
        # no-cache so an updated bundle is never masked by a stale browser copy.
        assert "no-cache" in response.headers.get("cache-control", ""), asset


def test_index_is_not_cached(client: tuple[TestClient, ProjectStore]) -> None:
    api, _ = client
    response = api.get("/")
    assert "no-cache" in response.headers.get("cache-control", "")
