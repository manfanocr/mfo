"""Optional-model tooling: catalog, cache location, and fetch helpers (spec §15; NFR-12, NFR-22).

mfo's core pipeline runs fully offline and downloads nothing (I-7/I-8). The *optional* engines —
the ML detector, manga-ocr, Argos translation, PaddleOCR — each need a model that lives outside the
repo. This module is the single registry of those assets plus the helpers the ``mfo models`` command
uses to locate, inspect, and fetch them into one shared cache directory (overridable with the
``MFO_MODEL_DIR`` env var), so a clean machine can provision everything from the docs alone.

Two flavours of asset:

* **Downloadable file** (``filename`` set) — a single weight file (e.g. the detector's ONNX export)
  mfo can fetch directly from a URL into the model dir, atomically (temp + rename, NFR-26/27).
* **Managed** (no ``filename``) — a model provisioned by its own library or package manager
  (manga-ocr pulls from Hugging Face on first use; Argos via ``argospm``). mfo can't fetch these
  itself, so the catalog records the one-line command instead of guessing a URL.
"""

from __future__ import annotations

import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# The detector adapter (mfo.vision.detect) imports these so the cache location and the file it looks
# for stay defined in exactly one place; pulling ``detector-onnx`` drops the file where it looks.
DEFAULT_DETECTOR_MODEL_FILENAME = "comic-text-detector.onnx"


def default_model_dir() -> Path:
    """Where optional model weights are cached (overridable via the ``MFO_MODEL_DIR`` env var)."""
    override = os.environ.get("MFO_MODEL_DIR")
    return Path(override) if override else Path.home() / ".cache" / "mfo" / "models"


class AssetError(RuntimeError):
    """A model asset could not be fetched (no URL, or the download failed)."""


class AssetStatus(StrEnum):
    """Whether a catalog asset is present locally."""

    CACHED = "cached"  # downloadable file present in the model dir
    MISSING = "missing"  # downloadable file not yet fetched
    MANAGED = "managed"  # provisioned by its own library/package manager, not by mfo


@dataclass(frozen=True)
class ModelAsset:
    """A named optional model mfo knows how to locate (and, when ``filename`` is set, fetch)."""

    name: str
    kind: str  # "detector" | "ocr" | "translate"
    summary: str
    extra: str = ""  # the pip extra that installs the engine, e.g. "detect"
    filename: str | None = None  # set => a single downloadable weight file
    url: str = ""  # default download URL (often empty; supply via --url / env)
    env_url_var: str | None = None  # env var that overrides ``url``
    install_hint: str = ""  # how to provision a *managed* asset

    @property
    def downloadable(self) -> bool:
        """True when mfo can fetch this asset directly into the model dir."""
        return self.filename is not None


@dataclass(frozen=True)
class PullResult:
    """Outcome of :func:`pull_asset`."""

    asset: ModelAsset
    status: AssetStatus
    path: Path | None = None
    downloaded: bool = False  # True only when this call performed the download


# The optional models mfo can provision or point at. The single source of truth so the CLI, docs,
# and engines agree on names.
CATALOG: tuple[ModelAsset, ...] = (
    ModelAsset(
        name="detector-onnx",
        kind="detector",
        summary="Comic text/bubble detector (ONNX) for `mfo detect --detector ml`.",
        extra="detect",
        filename=DEFAULT_DETECTOR_MODEL_FILENAME,
        env_url_var="MFO_DETECTOR_MODEL_URL",
        install_hint=(
            "pip install 'mfo[detect]', then "
            "`mfo models pull detector-onnx --url <onnx-export-url>` "
            "(or set MFO_DETECTOR_MODEL_URL)."
        ),
    ),
    ModelAsset(
        name="manga-ocr",
        kind="ocr",
        summary="manga-ocr Japanese recognizer; downloads from Hugging Face on first use.",
        extra="ocr",
        install_hint="pip install 'mfo[ocr]' (the model auto-downloads on the first `mfo ocr`).",
    ),
    ModelAsset(
        name="paddleocr",
        kind="ocr",
        summary="PaddleOCR detect+recognize; downloads its own models on first use.",
        extra="ocr-paddle",
        install_hint="pip install 'mfo[ocr-paddle]' (models auto-download on first use).",
    ),
    ModelAsset(
        name="argos-ja-en",
        kind="translate",
        summary="Argos offline JA->EN translation package.",
        extra="translate",
        install_hint="pip install 'mfo[translate]', then `argospm install translate-ja_en`.",
    ),
)


def iter_assets() -> tuple[ModelAsset, ...]:
    """The known optional-model catalog."""
    return CATALOG


def find_asset(name: str) -> ModelAsset | None:
    """Look up a catalog asset by name (exact match)."""
    return next((asset for asset in CATALOG if asset.name == name), None)


def resolved_url(asset: ModelAsset) -> str:
    """The effective download URL: an env override (``env_url_var``) wins over ``url``."""
    if asset.env_url_var:
        override = os.environ.get(asset.env_url_var)
        if override:
            return override
    return asset.url


def asset_path(asset: ModelAsset, *, model_dir: Path | None = None) -> Path:
    """The on-disk path a downloadable asset is cached at (raises for managed assets)."""
    if asset.filename is None:
        raise AssetError(f"{asset.name!r} is managed externally and has no cached file path")
    return (model_dir or default_model_dir()) / asset.filename


def asset_status(asset: ModelAsset, *, model_dir: Path | None = None) -> AssetStatus:
    """Whether the asset is cached locally, still missing, or managed by its own library."""
    if not asset.downloadable:
        return AssetStatus.MANAGED
    present = asset_path(asset, model_dir=model_dir).exists()
    return AssetStatus.CACHED if present else AssetStatus.MISSING


# A downloader fetches ``url`` into the destination path. Injectable so tests skip the network.
Downloader = Callable[[str, Path], None]


def _urlretrieve(url: str, dest: Path) -> None:
    urllib.request.urlretrieve(url, dest)  # noqa: S310 (user-configured URL)


def pull_asset(
    asset: ModelAsset,
    *,
    model_dir: Path | None = None,
    url: str | None = None,
    downloader: Downloader | None = None,
) -> PullResult:
    """Fetch a downloadable asset into the model dir (idempotent); report managed ones as such.

    Already-cached files are left untouched. Downloads are atomic (temp + rename) so an interrupted
    pull never leaves a half-written model behind. ``url`` overrides the catalog/env URL; managed
    assets (no ``filename``) return a ``MANAGED`` result for the caller to explain.
    """
    if not asset.downloadable:
        return PullResult(asset, AssetStatus.MANAGED)
    directory = model_dir or default_model_dir()
    dest = asset_path(asset, model_dir=directory)
    if dest.exists():
        return PullResult(asset, AssetStatus.CACHED, path=dest)
    src_url = url or resolved_url(asset)
    if not src_url:
        hint = f" or set {asset.env_url_var}" if asset.env_url_var else ""
        raise AssetError(f"no download URL for {asset.name!r}; pass --url{hint}")
    fetch = downloader or _urlretrieve
    directory.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        fetch(src_url, tmp)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise AssetError(f"failed to download {asset.name!r} from {src_url}: {exc}") from exc
    tmp.replace(dest)  # atomic publish
    return PullResult(asset, AssetStatus.CACHED, path=dest, downloaded=True)
