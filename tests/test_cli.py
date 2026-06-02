"""Tests for the Typer CLI."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from mfo.cli import app
from mfo.cli.stages import build_pipeline
from mfo.core import Page, Region
from mfo.storage import ProjectStore

runner = CliRunner()

_MANGA_OCR_INSTALLED = importlib.util.find_spec("manga_ocr") is not None


def _make_png(path: Path, size: tuple[int, int] = (3, 4)) -> None:
    Image.new("RGB", size, "white").save(path)


def _make_page_with_text(path: Path) -> None:
    """A white page with a solid black block the baseline detector will find."""
    image = Image.new("RGB", (200, 300), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 80, 60), fill="black")
    image.save(path)


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "mfo" in result.stdout


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "run", "status", "export", "review"):
        assert command in result.stdout


def test_init_creates_valid_project(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    result = runner.invoke(app, ["init", str(target), "--source", "ja", "--target", "en"])
    assert result.exit_code == 0, result.stdout
    with ProjectStore.open(target) as store:
        assert store.project.source_lang == "ja"
        assert store.project.target_lang == "en"
        assert store.project.name == "vol"


def test_init_defaults_name_to_dir(tmp_path: Path) -> None:
    target = tmp_path / "my-volume"
    runner.invoke(app, ["init", str(target)])
    with ProjectStore.open(target) as store:
        assert store.project.name == "my-volume"


def test_init_refuses_existing(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    assert runner.invoke(app, ["init", str(target)]).exit_code == 0
    assert runner.invoke(app, ["init", str(target)]).exit_code == 1


def test_status_reports_stage_state(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    with ProjectStore.open(target) as store:
        store.db.save(
            Page(project_id=store.project.id, index=0, image_path="a.png", width=1, height=1)
        )

    result = runner.invoke(app, ["status", str(target)])
    assert result.exit_code == 0
    assert "import" in result.stdout
    assert "1 pages" in result.stdout
    assert "pending" in result.stdout  # later stages have no data yet


def test_status_missing_project_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["status", str(tmp_path / "nope")])
    assert result.exit_code == 1


def test_run_without_import_config_is_a_noop(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["run", str(target)])
    assert result.exit_code == 0
    assert "Nothing to run yet" in result.stdout


def test_run_executes_import_and_preprocess_end_to_end(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "p1.png")
    _make_png(source / "p2.png")
    # `import`/`preprocess` record their config; `run` rebuilds the pipeline from it.
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["preprocess", str(target), "--max-dim", "2"])

    result = runner.invoke(app, ["run", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "import" in result.stdout
    assert "preprocess" in result.stdout
    assert "Pipeline complete." in result.stdout

    # A second run skips both stages (inputs unchanged → resumable/cacheable).
    again = runner.invoke(app, ["run", str(target)])
    assert "[skip] import" in again.stdout
    assert "[skip] preprocess" in again.stdout


def test_import_creates_pages_and_status_reports_them(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "p1.png")
    _make_png(source / "p2.png")

    result = runner.invoke(app, ["import", str(target), str(source)])
    assert result.exit_code == 0, result.stdout
    assert "Imported 2 page(s)" in result.stdout

    with ProjectStore.open(target) as store:
        assert len(store.db.list(Page)) == 2

    status = runner.invoke(app, ["status", str(target)])
    assert "2 pages" in status.stdout


def test_import_skips_corrupt_image(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "good.png")
    (source / "bad.png").write_bytes(b"not a png")

    result = runner.invoke(app, ["import", str(target), str(source)])
    assert result.exit_code == 0, result.stdout
    assert "Imported 1 page(s)" in result.stdout
    assert "skipped bad.png" in result.stdout


def test_import_missing_source_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["import", str(target), str(tmp_path / "nope")])
    assert result.exit_code == 1


def test_preprocess_builds_derivatives_and_status_reports(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])

    result = runner.invoke(app, ["preprocess", str(target), "--max-dim", "2"])
    assert result.exit_code == 0, result.stdout
    assert "Preprocessed 1 page(s)" in result.stdout

    with ProjectStore.open(target) as store:
        assert store.db.list(Page)[0].preprocessing.get("cache_key")

    status = runner.invoke(app, ["status", str(target)])
    assert "preprocess" in status.stdout


def test_detect_finds_regions_and_status_reports(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])

    result = runner.invoke(app, ["detect", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Detected 1 region(s)" in result.stdout

    with ProjectStore.open(target) as store:
        regions = store.db.list(Region)
        assert len(regions) == 1
        assert store.db.list(Page)[0].detection.get("signature")

    status = runner.invoke(app, ["status", str(target)])
    assert "detect" in status.stdout
    assert "1 regions" in status.stdout


def test_detect_unknown_detector_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["detect", str(target), "--detector", "nope"])
    assert result.exit_code == 1


def test_run_includes_detect_stage(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])

    result = runner.invoke(app, ["run", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "detect" in result.stdout
    with ProjectStore.open(target) as store:
        assert len(store.db.list(Region)) == 1


def test_ocr_command_persists_config_and_run_includes_ocr_stage(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_png(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])

    # No regions detected yet → 0 spans and no OCR engine model is loaded (no dependency needed).
    result = runner.invoke(app, ["ocr", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Recognized 0 region(s)" in result.stdout

    # Choosing an engine opts OCR into the pipeline (it is off until configured).
    with ProjectStore.open(target) as store:
        assert store.project.config["ocr"]["engine"] == "manga-ocr"
        assert "ocr" in build_pipeline(store).stage_names()


def test_ocr_unknown_engine_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["ocr", str(target), "--engine", "nope"])
    assert result.exit_code == 1


@pytest.mark.skipif(_MANGA_OCR_INSTALLED, reason="manga-ocr is installed; can't test its absence")
def test_ocr_missing_dependency_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])

    result = runner.invoke(app, ["ocr", str(target)])
    assert result.exit_code == 1
    assert "pip install" in result.output


def test_config_file_provides_defaults(tmp_path: Path) -> None:
    config = tmp_path / "mfo.toml"
    config.write_text('[mfo]\nsource_lang = "ko"\ntarget_lang = "en"\n')
    target = tmp_path / "vol"
    result = runner.invoke(app, ["init", str(target), "--config", str(config)])
    assert result.exit_code == 0, result.stdout
    with ProjectStore.open(target) as store:
        assert store.project.source_lang == "ko"


def test_cli_option_overrides_config_file(tmp_path: Path) -> None:
    config = tmp_path / "mfo.toml"
    config.write_text('[mfo]\nsource_lang = "ko"\n')
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target), "--config", str(config), "--source", "zh"])
    with ProjectStore.open(target) as store:
        assert store.project.source_lang == "zh"
