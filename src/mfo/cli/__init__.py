"""CLI layer: the headless, scriptable ``mfo`` entry point (spec FR-46)."""

from __future__ import annotations

from mfo.cli.app import app

__all__ = ["app", "main"]


def main() -> None:
    """Console-script entry point (see ``[project.scripts]`` in pyproject.toml)."""
    app()
