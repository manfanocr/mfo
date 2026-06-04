"""Tests for the optional-model catalog and fetch helpers (B8.11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mfo.core.assets import (
    DEFAULT_DETECTOR_MODEL_FILENAME,
    AssetError,
    AssetStatus,
    asset_path,
    asset_status,
    default_model_dir,
    find_asset,
    iter_assets,
    pull_asset,
    resolved_url,
)


def test_catalog_is_non_empty_and_has_unique_names() -> None:
    names = [asset.name for asset in iter_assets()]
    assert names
    assert len(names) == len(set(names))


def test_find_asset_returns_none_for_unknown() -> None:
    assert find_asset("does-not-exist") is None
    assert find_asset("detector-onnx") is not None


def test_default_model_dir_honors_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MFO_MODEL_DIR", str(tmp_path / "weights"))
    assert default_model_dir() == tmp_path / "weights"
    monkeypatch.delenv("MFO_MODEL_DIR", raising=False)
    assert default_model_dir() == Path.home() / ".cache" / "mfo" / "models"


def test_detector_asset_filename_matches_detector_default() -> None:
    detector = find_asset("detector-onnx")
    assert detector is not None
    assert detector.downloadable
    assert detector.filename == DEFAULT_DETECTOR_MODEL_FILENAME


def test_managed_asset_status_and_pull(tmp_path: Path) -> None:
    managed = find_asset("manga-ocr")
    assert managed is not None
    assert not managed.downloadable
    assert asset_status(managed, model_dir=tmp_path) is AssetStatus.MANAGED
    result = pull_asset(managed, model_dir=tmp_path)
    assert result.status is AssetStatus.MANAGED
    assert result.path is None


def test_pull_downloads_then_is_idempotent(tmp_path: Path) -> None:
    asset = find_asset("detector-onnx")
    assert asset is not None
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, dest: Path) -> None:
        calls.append((url, dest))
        dest.write_bytes(b"onnx-bytes")

    assert asset_status(asset, model_dir=tmp_path) is AssetStatus.MISSING
    first = pull_asset(
        asset, model_dir=tmp_path, url="https://example/model.onnx", downloader=fake_download
    )
    assert first.downloaded is True
    assert first.path == asset_path(asset, model_dir=tmp_path)
    assert first.path is not None and first.path.read_bytes() == b"onnx-bytes"
    assert asset_status(asset, model_dir=tmp_path) is AssetStatus.CACHED
    # The partial file is cleaned up on success.
    assert not first.path.with_name(first.path.name + ".part").exists()

    # Second pull is a no-op: cached, no download.
    second = pull_asset(
        asset, model_dir=tmp_path, url="https://example/model.onnx", downloader=fake_download
    )
    assert second.downloaded is False
    assert second.status is AssetStatus.CACHED
    assert len(calls) == 1


def test_pull_without_url_raises(tmp_path: Path) -> None:
    asset = find_asset("detector-onnx")
    assert asset is not None
    with pytest.raises(AssetError):
        pull_asset(asset, model_dir=tmp_path)


def test_failed_download_cleans_up_and_raises(tmp_path: Path) -> None:
    asset = find_asset("detector-onnx")
    assert asset is not None

    def boom(url: str, dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise OSError("network down")

    with pytest.raises(AssetError):
        pull_asset(asset, model_dir=tmp_path, url="https://example/model.onnx", downloader=boom)
    dest = asset_path(asset, model_dir=tmp_path)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_resolved_url_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    asset = find_asset("detector-onnx")
    assert asset is not None
    assert asset.env_url_var is not None
    monkeypatch.setenv(asset.env_url_var, "https://override/model.onnx")
    assert resolved_url(asset) == "https://override/model.onnx"


def test_asset_path_rejects_managed_assets(tmp_path: Path) -> None:
    managed = find_asset("manga-ocr")
    assert managed is not None
    with pytest.raises(AssetError):
        asset_path(managed, model_dir=tmp_path)
