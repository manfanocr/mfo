"""Tests for the Typer CLI."""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from mfo.cli import app
from mfo.cli.stages import build_pipeline
from mfo.core import (
    OCRSpan,
    Page,
    Region,
    RenderArtifact,
    TranslationCandidate,
    TranslationUnit,
)
from mfo.core.enums import CandidateKind, RegionStatus, RegionType
from mfo.core.geometry import BBox
from mfo.storage import ProjectStore

runner = CliRunner()

_MANGA_OCR_INSTALLED = importlib.util.find_spec("manga_ocr") is not None
_ARGOS_INSTALLED = importlib.util.find_spec("argostranslate") is not None


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


def test_import_from_cbz_archive_builds_ordered_project(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    archive = tmp_path / "volume.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in ("page1.png", "page2.png", "page10.png"):
            buffer = io.BytesIO()
            Image.new("RGB", (4, 5), "white").save(buffer, format="PNG")
            zf.writestr(name, buffer.getvalue())

    result = runner.invoke(app, ["import", str(target), str(archive)])
    assert result.exit_code == 0, result.stdout
    assert "Imported 3 page(s)" in result.stdout

    with ProjectStore.open(target) as store:
        pages = sorted(store.db.list(Page), key=lambda p: p.index)
        assert [Path(p.image_path).name for p in pages] == ["page1.png", "page2.png", "page10.png"]
    # Source archive never modified (I-1).
    assert archive.is_file()

    # `mfo run` replays the archive import from saved config; a second run skips it (resumable).
    assert "[run ] import" in runner.invoke(app, ["run", str(target)]).stdout
    assert "[skip] import" in runner.invoke(app, ["run", str(target)]).stdout


def test_import_rejects_non_archive_file(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    bogus = tmp_path / "notes.txt"
    bogus.write_text("not an archive")
    result = runner.invoke(app, ["import", str(target), str(bogus)])
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


def test_detect_reports_cache_reuse_on_rerun(tmp_path: Path) -> None:
    # A second run with the same detector skips unchanged pages (NFR-8): 0 *new* regions, but the
    # message must make clear the project still holds its regions (not mistaken for a failure).
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])

    rerun = runner.invoke(app, ["detect", str(target)])
    assert rerun.exit_code == 0, rerun.stdout
    assert "Detected 0 new region(s); 1 total in project" in rerun.stdout
    assert "--force" in rerun.stdout


def test_detect_unknown_detector_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["detect", str(target), "--detector", "nope"])
    assert result.exit_code == 1


def test_detect_jobs_flag_processes_pages_concurrently(tmp_path: Path) -> None:
    # --jobs is a performance knob: it must produce the same regions as a serial run (NFR-5/8).
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    for i in range(3):
        _make_page_with_text(source / f"p{i}.png")
    runner.invoke(app, ["import", str(target), str(source)])

    result = runner.invoke(app, ["detect", str(target), "--jobs", "3"])
    assert result.exit_code == 0, result.stdout
    assert "Detected 3 region(s)" in result.stdout
    with ProjectStore.open(target) as store:
        assert len(store.db.list(Region)) == 3


def test_bench_times_configured_heavy_stages(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    for i in range(2):
        _make_page_with_text(source / f"p{i}.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])
    runner.invoke(app, ["render", str(target)])

    result = runner.invoke(app, ["bench", str(target), "--jobs", "2"])
    assert result.exit_code == 0, result.stdout
    assert "Benchmarking" in result.stdout
    assert "with --jobs 2" in result.stdout
    # Heavy stages that are configured (detect + render/mask) are timed; total is reported.
    assert "detect" in result.stdout
    assert "render" in result.stdout
    assert "total" in result.stdout


def test_bench_unknown_stage_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])

    result = runner.invoke(app, ["bench", str(target), "--stage", "nope"])
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


def test_order_assigns_indices_and_status_reports(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])

    result = runner.invoke(app, ["order", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Ordered 1 region(s)" in result.stdout

    with ProjectStore.open(target) as store:
        assert store.db.list(Region)[0].reading_order_index == 0

    status = runner.invoke(app, ["status", str(target)])
    assert "order" in status.stdout
    assert "1 regions" in status.stdout


def test_order_persists_direction_config(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["order", str(target), "--direction", "ltr"])
    assert result.exit_code == 0, result.stdout
    with ProjectStore.open(target) as store:
        assert store.project.config["structure"]["direction"] == "ltr"


def test_run_includes_structure_stage(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])

    result = runner.invoke(app, ["run", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "structure" in result.stdout
    with ProjectStore.open(target) as store:
        assert store.db.list(Region)[0].reading_order_index is not None


def test_group_creates_units_and_status_reports(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])
    runner.invoke(app, ["order", str(target)])

    result = runner.invoke(app, ["group", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Grouped regions into 1 unit(s)" in result.stdout

    with ProjectStore.open(target) as store:
        units = store.db.list(TranslationUnit)
        assert len(units) == 1
        assert units[0].page_id == store.db.list(Page)[0].id

    status = runner.invoke(app, ["status", str(target)])
    assert "group" in status.stdout
    assert "1 units" in status.stdout


def test_group_persists_config(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["group", str(target), "--max-gap", "0.7"])
    assert result.exit_code == 0, result.stdout
    with ProjectStore.open(target) as store:
        assert store.project.config["group"]["max_gap_ratio"] == 0.7


def test_run_includes_group_stage(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])

    result = runner.invoke(app, ["run", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "group" in result.stdout
    with ProjectStore.open(target) as store:
        assert len(store.db.list(TranslationUnit)) == 1


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


def test_translate_persists_config_and_reports_units(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])

    # No units yet → 0 translated and no translator backend is loaded (no dependency needed).
    result = runner.invoke(app, ["translate", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Translated 0 unit(s)" in result.stdout
    with ProjectStore.open(target) as store:
        assert store.project.config["translate"]["translator"] == "argos"


def test_translate_unknown_translator_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["translate", str(target), "--translator", "nope"])
    assert result.exit_code == 1


def test_translate_api_adapter_persists_without_key(tmp_path: Path) -> None:
    # Selecting the opt-in API adapter (4.4) is config-only: with no units it loads the adapter but
    # makes no network call and needs no key (NFR-24). Only the name is persisted, never a secret.
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["translate", str(target), "--translator", "api"])
    assert result.exit_code == 0, result.stdout
    with ProjectStore.open(target) as store:
        config = store.project.config["translate"]
        assert config["translator"] == "api"
        assert (
            "api_key" not in config and "base_url" not in config
        )  # NFR-25: nothing secret on disk


def test_translate_persists_style(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["translate", str(target), "--style", "natural"])
    assert result.exit_code == 0, result.stdout
    assert "natural" in result.stdout
    with ProjectStore.open(target) as store:
        assert store.project.config["translate"]["style"] == "natural"


def test_assist_persists_config_and_reports_units(tmp_path: Path) -> None:
    # The AI layer is opt-in: with no units it persists the choices but loads no backend and makes
    # no network call (I-7, NFR-24). Nothing secret is written to disk (NFR-25).
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(
        app, ["assist", str(target), "--mode", "auto", "--min-confidence", "0.7"]
    )
    assert result.exit_code == 0, result.stdout
    assert "AI auto" in result.stdout
    with ProjectStore.open(target) as store:
        config = store.project.config["assist"]
        assert config["assistant"] == "llm"
        assert config["mode"] == "auto"
        assert config["min_confidence"] == 0.7
        assert "api_key" not in config and "base_url" not in config


def test_assist_unknown_assistant_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["assist", str(target), "--assistant", "nope"])
    assert result.exit_code == 1


def test_glossary_add_list_remove(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])

    add = runner.invoke(app, ["glossary", "add", str(target), "太郎", "Taro", "--alias", "Tarou"])
    assert add.exit_code == 0, add.stdout

    listed = runner.invoke(app, ["glossary", "list", str(target)])
    assert listed.exit_code == 0
    assert "太郎 -> Taro" in listed.stdout
    assert "Tarou" in listed.stdout

    with ProjectStore.open(target) as store:
        entries = store.project.config["glossary"]
        assert entries == [
            {"source": "太郎", "target": "Taro", "aliases": ["Tarou"], "notes": None}
        ]

    removed = runner.invoke(app, ["glossary", "remove", str(target), "太郎"])
    assert removed.exit_code == 0
    empty = runner.invoke(app, ["glossary", "list", str(target)])
    assert "No glossary entries" in empty.stdout


def test_glossary_remove_unknown_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    result = runner.invoke(app, ["glossary", "remove", str(target), "nope"])
    assert result.exit_code == 1


def test_glossary_add_replaces_same_source(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    runner.invoke(app, ["glossary", "add", str(target), "鬼", "ogre"])
    runner.invoke(app, ["glossary", "add", str(target), "鬼", "oni"])
    with ProjectStore.open(target) as store:
        entries = store.project.config["glossary"]
        assert len(entries) == 1
        assert entries[0]["target"] == "oni"


# -- series glossary: cross-volume terminology memory (batch 8.5; SG-2, SG-3) -----------------


def test_series_link_promote_and_list(tmp_path: Path) -> None:
    vol = tmp_path / "vol1"
    store_file = tmp_path / "series.json"
    runner.invoke(app, ["init", str(vol)])
    runner.invoke(app, ["glossary", "add", str(vol), "太郎", "Taro", "--alias", "Tarou"])

    link = runner.invoke(app, ["glossary", "series", "link", str(vol), str(store_file)])
    assert link.exit_code == 0, link.stdout
    assert store_file.exists()  # an empty store is created on link

    promote = runner.invoke(app, ["glossary", "promote", str(vol), "太郎"])
    assert promote.exit_code == 0, promote.stdout

    listed = runner.invoke(app, ["glossary", "series", "list", str(vol)])
    assert "太郎 -> Taro" in listed.stdout
    assert "Tarou" in listed.stdout


def test_series_promote_without_link_exits_1(tmp_path: Path) -> None:
    vol = tmp_path / "vol"
    runner.invoke(app, ["init", str(vol)])
    runner.invoke(app, ["glossary", "add", str(vol), "鬼", "oni"])
    result = runner.invoke(app, ["glossary", "promote", str(vol), "鬼"])
    assert result.exit_code == 1


def test_name_fixed_in_volume_one_is_enforced_in_volume_two(tmp_path: Path) -> None:
    # The DoD: a term promoted from vol1 is consulted by vol2 through the shared store (SG-2).
    store_file = tmp_path / "series.json"
    vol1, vol2 = tmp_path / "vol1", tmp_path / "vol2"
    for vol in (vol1, vol2):
        runner.invoke(app, ["init", str(vol), "--source", "ja", "--target", "en"])
        runner.invoke(app, ["glossary", "series", "link", str(vol), str(store_file)])

    runner.invoke(app, ["glossary", "add", str(vol1), "太郎", "Taro", "--alias", "Tarou"])
    runner.invoke(app, ["glossary", "promote", str(vol1), "太郎"])

    from mfo.cli.stages import load_effective_glossary
    from mfo.core.glossary import apply_glossary

    with ProjectStore.open(vol2) as store:
        effective = load_effective_glossary(store)
        # vol2 has no project glossary of its own, yet inherits the series term and enforces it.
        assert [(e.source, e.target) for e in effective] == [("太郎", "Taro")]
        assert apply_glossary("Tarou runs", "太郎は走る", effective) == "Taro runs"


def test_project_entry_overrides_series_entry(tmp_path: Path) -> None:
    store_file = tmp_path / "series.json"
    vol = tmp_path / "vol"
    runner.invoke(app, ["init", str(vol)])
    runner.invoke(app, ["glossary", "series", "link", str(vol), str(store_file)])
    # Series says "oni"; the project pins "demon" for the same source — project wins (SG-2).
    runner.invoke(app, ["glossary", "add", str(vol), "鬼", "oni"])
    runner.invoke(app, ["glossary", "promote", str(vol), "鬼"])
    runner.invoke(app, ["glossary", "add", str(vol), "鬼", "demon"])

    from mfo.cli.stages import load_effective_glossary

    with ProjectStore.open(vol) as store:
        effective = load_effective_glossary(store)
        assert [(e.source, e.target) for e in effective] == [("鬼", "demon")]


def test_series_export_import_round_trip(tmp_path: Path) -> None:
    src_store, share = tmp_path / "a.json", tmp_path / "share.json"
    vol1, vol2 = tmp_path / "vol1", tmp_path / "vol2"
    runner.invoke(app, ["init", str(vol1)])
    runner.invoke(app, ["glossary", "series", "link", str(vol1), str(src_store)])
    runner.invoke(app, ["glossary", "add", str(vol1), "鬼", "oni"])
    runner.invoke(app, ["glossary", "promote", str(vol1), "鬼"])

    export = runner.invoke(app, ["glossary", "series", "export", str(vol1), str(share)])
    assert export.exit_code == 0, export.stdout

    runner.invoke(app, ["init", str(vol2)])
    runner.invoke(app, ["glossary", "series", "link", str(vol2), str(tmp_path / "b.json")])
    imp = runner.invoke(app, ["glossary", "series", "import", str(vol2), str(share)])
    assert imp.exit_code == 0, imp.stdout
    assert "鬼 -> oni" in runner.invoke(app, ["glossary", "series", "list", str(vol2)]).stdout


def test_export_mapping_writes_json(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target), "--source", "ja", "--target", "en"])
    with ProjectStore.open(target) as store:
        page = Page(project_id=store.project.id, index=0, image_path="p0.png", width=10, height=10)
        store.db.save(page)
        region = Region(
            page_id=page.id, bbox=BBox(x=1, y=2, width=3, height=4), reading_order_index=0
        )
        store.db.save(region)
        store.db.save(OCRSpan(region_id=region.id, text="こんにちは", confidence=0.9))
        candidate = TranslationCandidate(text="Hello", kind=CandidateKind.RAW)
        store.db.save(
            TranslationUnit(
                page_id=page.id,
                ordered_region_ids=[region.id],
                source_bundle="こんにちは",
                candidates=[candidate],
                selected_candidate_id=candidate.id,
            )
        )

    result = runner.invoke(app, ["export", str(target), "--mapping"])
    assert result.exit_code == 0, result.output
    out = target / "exports" / "mapping.json"
    assert out.is_file()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    unit = loaded["units"][0]
    assert unit["translation"] == "Hello"
    assert unit["regions"][0]["ocr"][0]["text"] == "こんにちは"
    assert unit["regions"][0]["bbox"] == {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}


def test_export_mapping_custom_out_dir(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    out_dir = tmp_path / "dump"
    result = runner.invoke(app, ["export", str(target), "--mapping", "--out", str(out_dir)])
    assert result.exit_code == 0, result.output
    assert (out_dir / "mapping.json").is_file()


def _seed_translated_page(store: ProjectStore) -> Page:
    """A real page image on disk carrying one translated bubble unit (no optional deps needed)."""
    image_rel = "pages/p0.png"
    image_path = store.layout.root / image_rel
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 120), "white").save(image_path)
    page = Page(project_id=store.project.id, index=0, image_path=image_rel, width=200, height=120)
    store.db.save(page)
    region = Region(
        page_id=page.id,
        bbox=BBox(x=20, y=20, width=120, height=40),
        reading_order_index=0,
        type=RegionType.BUBBLE,
    )
    store.db.save(region)
    store.db.save(OCRSpan(region_id=region.id, text="こんにちは", confidence=0.9))
    candidate = TranslationCandidate(text="Hello there", kind=CandidateKind.RAW)
    store.db.save(
        TranslationUnit(
            page_id=page.id,
            ordered_region_ids=[region.id],
            source_bundle="こんにちは",
            candidates=[candidate],
            selected_candidate_id=candidate.id,
        )
    )
    return page


def test_export_composites_pages_and_writes_bundle(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target), "--source", "ja", "--target", "en"])
    with ProjectStore.open(target) as store:
        _seed_translated_page(store)

    result = runner.invoke(app, ["export", str(target)])
    assert result.exit_code == 0, result.output
    assert "Exported 1 page(s)" in result.output

    exports = target / "exports"
    assert (exports / "pages" / "0000.png").is_file()  # the translated page (MVP-9)
    assert (exports / "mapping.json").is_file()  # the source→OCR→translation mapping (FR-43)
    assert (exports / "manifest.json").is_file()
    assert (exports / "transcript.txt").is_file()

    manifest = json.loads((exports / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pages"][0]["source"] == "render"  # composited (onto the original here)

    with ProjectStore.open(target) as store:
        renders = [a for a in store.db.list(RenderArtifact) if a.params["kind"] == "render"]
        assert len(renders) == 1  # a render artifact was recorded and traced to the page (I-2)


def test_run_includes_composite_stage_once_render_and_translate_configured(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])

    # Render alone is not enough — compositing also needs translation configured.
    runner.invoke(app, ["render", str(target)])
    with ProjectStore.open(target) as store:
        assert "composite" not in build_pipeline(store).stage_names()

    runner.invoke(app, ["ocr", str(target)])  # translation depends on OCR being configured
    runner.invoke(app, ["translate", str(target)])
    with ProjectStore.open(target) as store:
        assert "composite" in build_pipeline(store).stage_names()


def test_run_includes_translate_stage_once_ocr_configured(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["ocr", str(target)])  # translation depends on OCR being configured
    runner.invoke(app, ["translate", str(target)])

    with ProjectStore.open(target) as store:
        names = build_pipeline(store).stage_names()
        assert "ocr" in names
        assert "translate" in names


@pytest.mark.skipif(_ARGOS_INSTALLED, reason="argostranslate is installed; can't test its absence")
def test_translate_missing_dependency_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    with ProjectStore.open(target) as store:
        page = Page(project_id=store.project.id, index=0, image_path="p0.png", width=10, height=10)
        store.db.save(page)
        region = Region(page_id=page.id, bbox=BBox(x=0, y=0, width=5, height=5))
        store.db.save(region)
        store.db.save(OCRSpan(region_id=region.id, text="こんにちは"))
        store.db.save(TranslationUnit(page_id=page.id, ordered_region_ids=[region.id]))

    result = runner.invoke(app, ["translate", str(target)])
    assert result.exit_code == 1
    assert "pip install" in result.output


def _save_region(store: ProjectStore, confidence: float | None) -> Region:
    region = Region(page_id="pg_x", bbox=BBox(x=0, y=0, width=1, height=1), confidence=confidence)
    store.db.save(region)
    return region


def test_status_reports_low_confidence(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    with ProjectStore.open(target) as store:
        _save_region(store, 0.9)
        _save_region(store, 0.2)

    result = runner.invoke(app, ["status", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Confidence:" in result.stdout
    assert "low-confidence: 1/2" in result.stdout


def test_flag_marks_low_confidence_regions(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    with ProjectStore.open(target) as store:
        high = _save_region(store, 0.9)
        low = _save_region(store, 0.2)

    result = runner.invoke(app, ["flag", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Flagged 1 region(s)" in result.stdout

    with ProjectStore.open(target) as store:
        flagged_region = store.db.get(Region, low.id)
        high_region = store.db.get(Region, high.id)
        assert flagged_region is not None and flagged_region.status is RegionStatus.NEEDS_REVIEW
        assert high_region is not None and high_region.status is RegionStatus.AUTO

    status = runner.invoke(app, ["status", str(target)])
    assert "flagged for review: 1" in status.stdout


def test_flag_threshold_option(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    with ProjectStore.open(target) as store:
        _save_region(store, 0.6)

    # A higher threshold pulls the 0.6 region into the low-confidence set.
    result = runner.invoke(app, ["flag", str(target), "--threshold", "0.7"])
    assert "Flagged 1 region(s)" in result.stdout


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


def test_render_masks_pages_and_status_reports(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])

    result = runner.invoke(app, ["render", str(target)])
    assert result.exit_code == 0, result.stdout
    assert "Masked 1 page(s)" in result.stdout

    with ProjectStore.open(target) as store:
        artifacts = store.db.list(RenderArtifact)
        assert len(artifacts) == 1
        masked = store.layout.root / artifacts[0].output_path
        assert masked.is_file()
        assert store.project.config["render"]["pad"] == 2

    status = runner.invoke(app, ["status", str(target)])
    assert "render" in status.stdout
    assert "1 pages" in status.stdout


def test_run_includes_render_stage_once_configured(tmp_path: Path) -> None:
    target = tmp_path / "vol"
    runner.invoke(app, ["init", str(target)])
    source = tmp_path / "src"
    source.mkdir()
    _make_page_with_text(source / "p1.png")
    runner.invoke(app, ["import", str(target), str(source)])
    runner.invoke(app, ["detect", str(target)])

    with ProjectStore.open(target) as store:
        assert "render" not in build_pipeline(store).stage_names()

    # Configuring render via `mfo render` makes it join the pipeline.
    runner.invoke(app, ["render", str(target)])
    with ProjectStore.open(target) as store:
        assert "render" in build_pipeline(store).stage_names()

    result = runner.invoke(app, ["run", str(target), "--force"])
    assert result.exit_code == 0, result.stdout
    assert "render" in result.stdout
    with ProjectStore.open(target) as store:
        assert len(store.db.list(RenderArtifact)) == 1
