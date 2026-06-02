"""Tests for the Typer CLI."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from mfo.cli import app
from mfo.core import Page
from mfo.storage import ProjectStore

runner = CliRunner()


def _make_png(path: Path, size: tuple[int, int] = (3, 4)) -> None:
    Image.new("RGB", size, "white").save(path)


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


def test_run_on_valid_project_with_empty_pipeline(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["run", str(target)])
    assert result.exit_code == 0
    assert "No pipeline stages" in result.stdout


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
