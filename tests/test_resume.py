"""End-to-end resume & project save (batch 1.3; FR-5, FR-48, MVP-10; NFR-10/11).

Simulates an import that was killed partway — some pages copied, no stage-completion record
written — then reopens the project and runs the pipeline. The run must finish the import without
re-copying the pages already brought in, then preprocess everything, and a subsequent run must
skip both stages entirely.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from mfo.cli import app
from mfo.cli.stages import save_import_config
from mfo.core import Page
from mfo.storage import ProjectStore, import_pages
from mfo.vision import PageOrder, discover_images

runner = CliRunner()


def _make_source(source: Path, count: int) -> None:
    source.mkdir()
    for i in range(1, count + 1):
        Image.new("RGB", (8, 6), "white").save(source / f"p{i}.png")


def test_run_resumes_interrupted_import_without_redoing_pages(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    source = tmp_path / "src"
    _make_source(source, 3)
    runner.invoke(app, ["init", str(target)])

    # Simulate a killed `mfo import`: the source was recorded and two of three pages copied,
    # but the process died before the import stage's completion record was written.
    with ProjectStore.open(target) as store:
        save_import_config(store, source=source, order=PageOrder.NATURAL, manifest_order=None)
        partial = discover_images(source, order=PageOrder.NATURAL).images[:2]
        import_pages(store, partial)

    copied = sorted(target.joinpath("pages").glob("*.png"))
    assert [p.name for p in copied] == ["p1.png", "p2.png"]
    before_mtimes = {p.name: p.stat().st_mtime_ns for p in copied}

    # Reopen + run: import finishes (p3 added), preprocess covers all pages.
    result = runner.invoke(app, ["run", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Pipeline complete." in result.stdout

    with ProjectStore.open(target) as store:
        pages = store.db.list(Page, order_by="idx")
        assert [Path(p.image_path).name for p in pages] == ["p1.png", "p2.png", "p3.png"]
        assert all(p.preprocessing.get("cache_key") for p in pages)

    # The two already-imported originals were not re-copied (completed work not redone).
    after_mtimes = {p.name: p.stat().st_mtime_ns for p in target.joinpath("pages").glob("*.png")}
    assert after_mtimes["p1.png"] == before_mtimes["p1.png"]
    assert after_mtimes["p2.png"] == before_mtimes["p2.png"]

    # Running again is a clean no-op: both stages skip.
    again = runner.invoke(app, ["run", str(target)])
    assert "[skip] import" in again.stdout
    assert "[skip] preprocess" in again.stdout


def test_adding_a_page_reimports_and_repreprocesses(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    source = tmp_path / "src"
    _make_source(source, 2)
    runner.invoke(app, ["init", str(target)])
    runner.invoke(app, ["import", str(target), str(source)])
    # First run establishes the stage-completion records; a second run then skips.
    runner.invoke(app, ["run", str(target)])
    assert "[skip] import" in runner.invoke(app, ["run", str(target)]).stdout

    # A new source page changes the import inputs hash → import + preprocess re-run.
    Image.new("RGB", (8, 6), "white").save(source / "p3.png")
    result = runner.invoke(app, ["run", str(target)])
    assert "[run ] import" in result.stdout
    assert "[run ] preprocess" in result.stdout

    with ProjectStore.open(target) as store:
        pages = store.db.list(Page, order_by="idx")
        assert len(pages) == 3
        assert all(p.preprocessing.get("cache_key") for p in pages)
