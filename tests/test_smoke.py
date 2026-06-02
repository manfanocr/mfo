"""Smoke tests confirming the package imports and the scaffolding is wired up."""

from __future__ import annotations

import importlib

import mfo
from mfo.cli import main


def test_version_is_a_string() -> None:
    assert isinstance(mfo.__version__, str)
    assert mfo.__version__


def test_all_layers_import() -> None:
    for layer in ("core", "vision", "language", "render", "storage", "ui", "cli"):
        importlib.import_module(f"mfo.{layer}")


def test_cli_version_returns_zero() -> None:
    assert main(["--version"]) == 0


def test_cli_default_returns_zero() -> None:
    assert main([]) == 0
