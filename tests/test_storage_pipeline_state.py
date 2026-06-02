"""Tests for the on-disk pipeline state store (resume across process restarts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mfo.core.pipeline import Pipeline, Stage, StageRecord, StageStatus
from mfo.storage import JsonStateStore


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state.json")
    assert store.load() == {}


def test_record_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    JsonStateStore(path).save(
        StageRecord(name="ocr", input_hash="abc", status=StageStatus.DONE, updated_at="t0")
    )

    # A fresh instance (simulating a new process) reads the persisted record.
    loaded = JsonStateStore(path).load()
    assert loaded["ocr"] == StageRecord(
        name="ocr", input_hash="abc", status=StageStatus.DONE, updated_at="t0"
    )


def test_saving_preserves_other_records(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state.json")
    store.save(StageRecord("a", "h1", StageStatus.DONE, "t0"))
    store.save(StageRecord("b", "h2", StageStatus.DONE, "t1"))
    assert set(store.load()) == {"a", "b"}


@dataclass
class Ctx:
    log: list[str] = field(default_factory=list)


@dataclass
class RecordingStage:
    name: str
    deps: tuple[str, ...] = ()

    def inputs_hash(self, ctx: Ctx) -> str:
        return self.name

    def run(self, ctx: Ctx) -> None:
        ctx.log.append(self.name)


class RaisingStage:
    def __init__(self, name: str, deps: tuple[str, ...] = ()) -> None:
        self.name = name
        self.deps = deps

    def inputs_hash(self, ctx: Ctx) -> str:
        return self.name

    def run(self, ctx: Ctx) -> None:
        raise RuntimeError("boom")


def test_resume_uses_persisted_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    # First "process": 'a' completes and is flushed to disk, 'b' crashes.
    ctx1 = Ctx()
    broken: list[Stage[Ctx]] = [RecordingStage("a"), RaisingStage("b", deps=("a",))]
    with pytest.raises(RuntimeError):
        Pipeline(broken).run(ctx1, JsonStateStore(path))
    assert ctx1.log == ["a"]

    # Second "process": a brand-new state store reads the same file and skips 'a'.
    ctx2 = Ctx()
    fixed: list[Stage[Ctx]] = [RecordingStage("a"), RecordingStage("b", deps=("a",))]
    results = Pipeline(fixed).run(ctx2, JsonStateStore(path))
    assert [(r.name, r.skipped) for r in results] == [("a", True), ("b", False)]
    assert ctx2.log == ["b"]
