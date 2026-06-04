"""Tests for the `mfo models` and `mfo sample` CLI surfaces (B8.11)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from mfo.cli import app

runner = CliRunner()


def test_models_path_reports_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MFO_MODEL_DIR", str(tmp_path / "weights"))
    result = runner.invoke(app, ["models", "path"])
    assert result.exit_code == 0
    assert str(tmp_path / "weights") in result.stdout
    assert "MFO_MODEL_DIR" in result.stdout


def test_models_list_shows_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MFO_MODEL_DIR", str(tmp_path))
    result = runner.invoke(app, ["models", "list"])
    assert result.exit_code == 0
    assert "detector-onnx" in result.stdout
    assert "manga-ocr" in result.stdout
    # Nothing has been pulled, so the downloadable detector is missing and manga-ocr is managed.
    assert "missing" in result.stdout
    assert "managed" in result.stdout


def test_models_pull_managed_prints_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MFO_MODEL_DIR", str(tmp_path))
    result = runner.invoke(app, ["models", "pull", "manga-ocr"])
    assert result.exit_code == 0
    assert "mfo[ocr]" in result.stdout


def test_models_pull_downloadable_without_url_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MFO_MODEL_DIR", str(tmp_path))
    result = runner.invoke(app, ["models", "pull", "detector-onnx"])
    # No URL configured and none passed -> a clear failure, and nothing written to the cache.
    assert result.exit_code == 1
    assert not list(tmp_path.glob("*"))


def test_models_pull_unknown_asset_fails() -> None:
    result = runner.invoke(app, ["models", "pull", "nope"])
    assert result.exit_code == 1
