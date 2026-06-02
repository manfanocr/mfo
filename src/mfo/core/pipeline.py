"""Pipeline orchestrator (spec §10, §14.2; invariant I-5; FR-5; NFR-7/8).

A :class:`Pipeline` runs an ordered set of :class:`Stage` objects. Each stage declares its
dependencies and a content ``inputs_hash``; the orchestrator combines a stage's own hash with
its dependencies' *effective keys* so that any upstream change invalidates everything
downstream. Completed stages are recorded (via a :class:`StateStore`) keyed by that effective
key, so a re-run skips stages whose inputs are unchanged and resumes after an interruption —
the interrupted stage simply has no completed record and runs again.

Stages are intentionally opaque to the orchestrator: they communicate only through persisted
project state (read inputs from / write outputs to storage), never via in-memory handoffs, so
any stage can be re-run alone. The orchestrator therefore depends on nothing outside ``core``;
the context type ``Ctx`` is supplied by the caller (e.g. the CLI passes a project store).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

Ctx = TypeVar("Ctx")
Ctx_contra = TypeVar("Ctx_contra", contravariant=True)


class Stage(Protocol[Ctx_contra]):
    """A single pipeline stage.

    ``inputs_hash`` must be a pure function of everything the stage reads (config, model
    versions, upstream data); the orchestrator folds dependency keys in on top of it.
    """

    name: str
    deps: tuple[str, ...]

    def inputs_hash(self, ctx: Ctx_contra) -> str: ...

    def run(self, ctx: Ctx_contra) -> None: ...


class StageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"


@dataclass(frozen=True)
class StageRecord:
    """The persisted result of running a stage: which inputs it was run against."""

    name: str
    input_hash: str
    status: StageStatus
    updated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "input_hash": self.input_hash,
            "status": self.status.value,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> StageRecord:
        return cls(
            name=data["name"],
            input_hash=data["input_hash"],
            status=StageStatus(data["status"]),
            updated_at=data["updated_at"],
        )


@dataclass(frozen=True)
class StageResult:
    """The outcome of a stage within a single ``Pipeline.run`` call."""

    name: str
    skipped: bool
    input_hash: str


class StateStore(Protocol):
    """Persists per-stage records so runs can skip unchanged stages and resume."""

    def load(self) -> dict[str, StageRecord]: ...

    def save(self, record: StageRecord) -> None: ...


class InMemoryStateStore:
    """A non-persistent :class:`StateStore`, useful for tests and one-shot runs."""

    def __init__(self) -> None:
        self._records: dict[str, StageRecord] = {}

    def load(self) -> dict[str, StageRecord]:
        return dict(self._records)

    def save(self, record: StageRecord) -> None:
        self._records[record.name] = record


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _combine(*parts: str) -> str:
    """Hash an ordered list of strings into one stable key (length-prefixed, no collisions)."""
    digest = hashlib.sha256()
    for part in parts:
        raw = part.encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _topo_order(stages: Sequence[Stage[Ctx]]) -> list[Stage[Ctx]]:
    """Return ``stages`` in dependency order, raising on duplicates, unknown deps, or cycles."""
    by_name: dict[str, Stage[Ctx]] = {}
    for stage in stages:
        if stage.name in by_name:
            raise ValueError(f"duplicate stage name {stage.name!r}")
        by_name[stage.name] = stage
    for stage in stages:
        for dep in stage.deps:
            if dep not in by_name:
                raise ValueError(f"stage {stage.name!r} depends on unknown stage {dep!r}")

    ordered: list[Stage[Ctx]] = []
    state: dict[str, int] = {}  # 0 = on the current path, 1 = finished

    def visit(stage: Stage[Ctx]) -> None:
        marker = state.get(stage.name)
        if marker == 1:
            return
        if marker == 0:
            raise ValueError(f"dependency cycle involving stage {stage.name!r}")
        state[stage.name] = 0
        for dep in stage.deps:
            visit(by_name[dep])
        state[stage.name] = 1
        ordered.append(stage)

    for stage in stages:
        visit(stage)
    return ordered


class Pipeline(Generic[Ctx]):
    """An ordered, dependency-resolved collection of stages."""

    def __init__(self, stages: Sequence[Stage[Ctx]]) -> None:
        self._stages = _topo_order(stages)
        self._by_name = {stage.name: stage for stage in self._stages}

    @property
    def stages(self) -> tuple[Stage[Ctx], ...]:
        return tuple(self._stages)

    def stage_names(self) -> list[str]:
        return [stage.name for stage in self._stages]

    def _require(self, name: str) -> Stage[Ctx]:
        try:
            return self._by_name[name]
        except KeyError:
            raise ValueError(f"unknown stage {name!r}; known: {self.stage_names()}") from None

    # -- input hashing --------------------------------------------------------------------

    def effective_key(self, name: str, ctx: Ctx) -> str:
        """The cache key for a stage: its own input hash folded with its dependencies'."""
        return self._effective_key(name, ctx, {})

    def _effective_key(self, name: str, ctx: Ctx, memo: dict[str, str]) -> str:
        cached = memo.get(name)
        if cached is not None:
            return cached
        stage = self._require(name)
        parts = [name, stage.inputs_hash(ctx)]
        parts.extend(self._effective_key(dep, ctx, memo) for dep in stage.deps)
        key = _combine(*parts)
        memo[name] = key
        return key

    # -- selection ------------------------------------------------------------------------

    def _downstream(self, name: str) -> set[str]:
        """``name`` plus every stage that (transitively) depends on it."""
        chosen = {name}
        changed = True
        while changed:
            changed = False
            for stage in self._stages:
                if stage.name not in chosen and any(dep in chosen for dep in stage.deps):
                    chosen.add(stage.name)
                    changed = True
        return chosen

    def _upstream(self, name: str) -> set[str]:
        """``name`` plus every stage it (transitively) depends on."""
        chosen: set[str] = set()

        def visit(current: str) -> None:
            if current in chosen:
                return
            chosen.add(current)
            for dep in self._require(current).deps:
                visit(dep)

        visit(name)
        return chosen

    def select(
        self,
        *,
        only: str | None = None,
        from_: str | None = None,
        to: str | None = None,
    ) -> list[Stage[Ctx]]:
        """Resolve ``--stage``/``--from``/``--to`` into the ordered stages to execute."""
        if only is not None and (from_ is not None or to is not None):
            raise ValueError("'only' (--stage) cannot be combined with 'from'/'to'")
        if only is not None:
            self._require(only)
            chosen = {only}
        else:
            chosen = set(self.stage_names())
            if from_ is not None:
                self._require(from_)
                chosen &= self._downstream(from_)
            if to is not None:
                self._require(to)
                chosen &= self._upstream(to)
        return [stage for stage in self._stages if stage.name in chosen]

    # -- execution ------------------------------------------------------------------------

    def run(
        self,
        ctx: Ctx,
        state: StateStore,
        *,
        only: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        force: bool = False,
    ) -> list[StageResult]:
        """Run the selected stages, skipping those whose inputs are unchanged.

        Effective keys are computed for *every* stage (so downstream keys reflect upstream
        inputs) but only selected stages execute. Each completed stage is persisted
        immediately, so an interrupted run resumes from where it stopped.
        """
        records = state.load()
        selected = {stage.name for stage in self.select(only=only, from_=from_, to=to)}
        memo: dict[str, str] = {}
        results: list[StageResult] = []
        for stage in self._stages:
            key = self._effective_key(stage.name, ctx, memo)
            if stage.name not in selected:
                continue
            existing = records.get(stage.name)
            if (
                not force
                and existing is not None
                and existing.status is StageStatus.DONE
                and existing.input_hash == key
            ):
                results.append(StageResult(name=stage.name, skipped=True, input_hash=key))
                continue
            stage.run(ctx)
            record = StageRecord(
                name=stage.name,
                input_hash=key,
                status=StageStatus.DONE,
                updated_at=_utcnow_iso(),
            )
            state.save(record)
            records[stage.name] = record
            results.append(StageResult(name=stage.name, skipped=False, input_hash=key))
        return results
