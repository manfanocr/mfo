"""End-to-end smoke run over the bundled synthetic sample dataset (B8.11; §21, NFR-28).

This exercises the fully-offline path — sample → init → import → preprocess → detect (baseline) →
order → group → export — that a clean machine can run from the docs alone, with no model downloads
and no optional dependencies. (OCR/translation need their own models, covered by their own tests.)
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from mfo.cli import app
from mfo.sample import create_sample_pages

runner = CliRunner()


def _run(*args: str) -> None:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, f"`mfo {' '.join(args)}` failed:\n{result.stdout}"


def test_create_sample_pages_are_deterministic(tmp_path: Path) -> None:
    first = create_sample_pages(tmp_path / "a", count=3)
    second = create_sample_pages(tmp_path / "b", count=3)
    assert len(first) == 3
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]


def test_sample_command_writes_pages(tmp_path: Path) -> None:
    pages = tmp_path / "pages"
    result = runner.invoke(app, ["sample", str(pages), "--pages", "2"])
    assert result.exit_code == 0
    assert sorted(p.name for p in pages.glob("*.png")) == ["page-01.png", "page-02.png"]
    assert "Next steps" in result.stdout


def test_offline_pipeline_runs_end_to_end(tmp_path: Path) -> None:
    pages_dir = tmp_path / "pages"
    create_sample_pages(pages_dir, count=2)
    project = tmp_path / "proj"
    out = tmp_path / "out"

    _run("init", str(project), "--source", "ja", "--target", "en")
    _run("import", str(project), str(pages_dir))
    _run("preprocess", str(project))
    _run("detect", str(project))
    _run("order", str(project))
    _run("group", str(project))
    _run("export", str(project), "--out", str(out))

    mapping = out / "mapping.json"
    assert mapping.exists()
    json.loads(mapping.read_text())  # valid JSON
    assert list((out / "pages").glob("*.png")), "export produced no page images"
