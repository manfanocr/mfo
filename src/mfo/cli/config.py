"""Configuration loading for repeatable runs (spec FR-47).

Settings are layered: built-in defaults < TOML config file < explicit CLI options. The config
file may put keys at the top level or under a ``[mfo]`` table. Unknown keys are rejected so
typos surface immediately (NFR-12).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from mfo.core import ReadingDirection


class Settings(BaseModel):
    """Resolved configuration used to create and run projects."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    source_lang: str = "ja"
    target_lang: str = "en"
    reading_direction: ReadingDirection = ReadingDirection.RTL
    log_level: str = "INFO"


def load_config_file(path: Path) -> dict[str, Any]:
    """Read a TOML config file, returning the ``[mfo]`` table if present, else the top level."""
    with Path(path).open("rb") as handle:
        data = tomllib.load(handle)
    section = data.get("mfo", data)
    if not isinstance(section, dict):
        raise ValueError(f"'mfo' section in {path} must be a table")
    return {str(key): value for key, value in section.items()}


def build_settings(config_path: Path | None = None, **overrides: Any) -> Settings:
    """Merge config file values with non-``None`` CLI overrides into validated ``Settings``."""
    data: dict[str, Any] = {}
    if config_path is not None:
        data.update(load_config_file(config_path))
    for key, value in overrides.items():
        if value is not None:
            data[key] = value
    return Settings(**data)
