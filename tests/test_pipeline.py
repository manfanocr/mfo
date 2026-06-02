"""Tests for the pipeline orchestrator (I-5, FR-5, NFR-7/8)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mfo.core.pipeline import (
    InMemoryStateStore,
    Pipeline,
    Stage,
    StageStatus,
)


@dataclass
class Ctx:
    """A trivial context: a value stages may read, and a log of which stages ran."""

    value: str = "v1"
    log: list[str] = field(default_factory=list)


@dataclass
class RecordingStage:
    """A dummy stage. Its input hash optionally tracks ``ctx.value``."""

    name: str
    deps: tuple[str, ...] = ()
    reads_value: bool = False

    def inputs_hash(self, ctx: Ctx) -> str:
        return ctx.value if self.reads_value else self.name

    def run(self, ctx: Ctx) -> None:
        ctx.log.append(self.name)


class RaisingStage:
    """A stage that fails when run, to simulate an interruption."""

    def __init__(self, name: str, deps: tuple[str, ...] = ()) -> None:
        self.name = name
        self.deps = deps

    def inputs_hash(self, ctx: Ctx) -> str:
        return self.name

    def run(self, ctx: Ctx) -> None:
        raise RuntimeError("boom")


def test_runs_in_dependency_order() -> None:
    stages: list[Stage[Ctx]] = [
        RecordingStage("b", deps=("a",)),
        RecordingStage("a"),
    ]
    pipeline = Pipeline(stages)
    ctx = Ctx()
    pipeline.run(ctx, InMemoryStateStore())
    assert ctx.log == ["a", "b"]


def test_skips_unchanged_stages_on_rerun() -> None:
    pipeline = Pipeline([RecordingStage("a"), RecordingStage("b", deps=("a",))])
    state = InMemoryStateStore()
    ctx = Ctx()

    first = pipeline.run(ctx, state)
    assert [r.skipped for r in first] == [False, False]
    assert ctx.log == ["a", "b"]

    second = pipeline.run(ctx, state)
    assert [r.skipped for r in second] == [True, True]
    assert ctx.log == ["a", "b"]  # nothing re-ran


def test_changed_input_invalidates_downstream() -> None:
    # 'a' reads ctx.value; 'b' does not, but depends on 'a'.
    pipeline = Pipeline(
        [
            RecordingStage("a", reads_value=True),
            RecordingStage("b", deps=("a",)),
        ]
    )
    state = InMemoryStateStore()
    ctx = Ctx(value="v1")
    pipeline.run(ctx, state)
    assert ctx.log == ["a", "b"]

    ctx.value = "v2"
    results = pipeline.run(ctx, state)
    assert [r.skipped for r in results] == [False, False]  # a changed → b re-runs too
    assert ctx.log == ["a", "b", "a", "b"]


def test_force_reruns_everything() -> None:
    pipeline = Pipeline([RecordingStage("a"), RecordingStage("b", deps=("a",))])
    state = InMemoryStateStore()
    ctx = Ctx()
    pipeline.run(ctx, state)
    results = pipeline.run(ctx, state, force=True)
    assert [r.skipped for r in results] == [False, False]
    assert ctx.log == ["a", "b", "a", "b"]


def test_resume_after_interruption() -> None:
    state = InMemoryStateStore()
    ctx = Ctx()

    # First run: 'a' succeeds, 'b' blows up mid-pipeline.
    broken: list[Stage[Ctx]] = [RecordingStage("a"), RaisingStage("b", deps=("a",))]
    with pytest.raises(RuntimeError):
        Pipeline(broken).run(ctx, state)
    assert ctx.log == ["a"]
    assert state.load()["a"].status is StageStatus.DONE
    assert "b" not in state.load()

    # Resume with a working 'b': 'a' is skipped, only 'b' runs.
    fixed: list[Stage[Ctx]] = [RecordingStage("a"), RecordingStage("b", deps=("a",))]
    results = Pipeline(fixed).run(ctx, state)
    assert [(r.name, r.skipped) for r in results] == [("a", True), ("b", False)]
    assert ctx.log == ["a", "b"]


def test_select_only_runs_one_stage() -> None:
    pipeline = Pipeline(
        [RecordingStage("a"), RecordingStage("b", deps=("a",)), RecordingStage("c", deps=("b",))]
    )
    ctx = Ctx()
    pipeline.run(ctx, InMemoryStateStore(), only="b")
    assert ctx.log == ["b"]


def test_select_from_runs_downstream() -> None:
    pipeline = Pipeline(
        [RecordingStage("a"), RecordingStage("b", deps=("a",)), RecordingStage("c", deps=("b",))]
    )
    ctx = Ctx()
    pipeline.run(ctx, InMemoryStateStore(), from_="b")
    assert ctx.log == ["b", "c"]


def test_select_to_runs_upstream() -> None:
    pipeline = Pipeline(
        [RecordingStage("a"), RecordingStage("b", deps=("a",)), RecordingStage("c", deps=("b",))]
    )
    ctx = Ctx()
    pipeline.run(ctx, InMemoryStateStore(), to="b")
    assert ctx.log == ["a", "b"]


def test_select_from_and_to_window() -> None:
    pipeline = Pipeline(
        [
            RecordingStage("a"),
            RecordingStage("b", deps=("a",)),
            RecordingStage("c", deps=("b",)),
            RecordingStage("d", deps=("c",)),
        ]
    )
    ctx = Ctx()
    pipeline.run(ctx, InMemoryStateStore(), from_="b", to="c")
    assert ctx.log == ["b", "c"]


def test_only_combined_with_from_raises() -> None:
    pipeline = Pipeline([RecordingStage("a")])
    with pytest.raises(ValueError, match="cannot be combined"):
        pipeline.run(Ctx(), InMemoryStateStore(), only="a", from_="a")


def test_unknown_stage_selection_raises() -> None:
    pipeline = Pipeline([RecordingStage("a")])
    with pytest.raises(ValueError, match="unknown stage"):
        pipeline.run(Ctx(), InMemoryStateStore(), only="nope")


def test_duplicate_stage_name_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate stage name"):
        Pipeline([RecordingStage("a"), RecordingStage("a")])


def test_unknown_dependency_rejected() -> None:
    with pytest.raises(ValueError, match="unknown stage"):
        Pipeline([RecordingStage("a", deps=("ghost",))])


def test_dependency_cycle_rejected() -> None:
    with pytest.raises(ValueError, match="cycle"):
        Pipeline([RecordingStage("a", deps=("b",)), RecordingStage("b", deps=("a",))])


def test_effective_key_is_stable_and_dependency_sensitive() -> None:
    pipeline = Pipeline([RecordingStage("a", reads_value=True), RecordingStage("b", deps=("a",))])
    ctx = Ctx(value="v1")
    key_b = pipeline.effective_key("b", ctx)
    assert key_b == pipeline.effective_key("b", ctx)  # stable
    ctx.value = "v2"
    assert pipeline.effective_key("b", ctx) != key_b  # upstream change propagates
