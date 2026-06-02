# Changelog

All notable changes to mfo are recorded here. Landed **batches** (from [PLAN.md](PLAN.md)) are
moved here when complete, with the spec IDs they satisfied.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches `0.1.0`.

## [Unreleased]

### Added
- **Batch 0.2 — Core data model** (M0 Foundation):
  - `mfo.core` entities (Pydantic v2): `Project, Page, Region, OCRSpan, TranslationCandidate,
    TranslationUnit, EditRecord, RenderArtifact`, plus geometry primitives (`BBox`, `Point`) and
    enums (region type/status, reading direction, translation style, candidate kind, edit action).
    Models forbid unknown fields and round-trip losslessly to/from JSON.
  - Dependency-free ULID identifier scheme (`mfo.core.ids`) with self-describing per-entity
    prefixes (e.g. `rgn_…`, `tu_…`) that are unique and time-sortable.
  - Integrity check: a `TranslationUnit`'s `selected_candidate_id` must reference one of its
    candidates.
  - Tests: ID format/uniqueness/sortability and Hypothesis property-based lossless round-trip
    for models. Adds `pydantic` (runtime) and `hypothesis` (dev) dependencies.
  - Satisfies: I-2, FR-41, NFR-30; spec §11.
- **Batch 0.1 — Repo scaffolding & tooling** (M0 Foundation):
  - `pyproject.toml` with hatchling build backend, src layout, package `mfo`, dev extras
    (pytest/ruff/mypy/pre-commit), and the `mfo` console script.
  - Layered package skeleton `src/mfo/{core,vision,language,render,storage,cli,ui}` per spec §15,
    with a `py.typed` marker and a placeholder CLI entry point (full CLI in batch 0.4).
  - Tooling config: ruff (lint + format), mypy `--strict`, pytest; `.editorconfig`,
    `.pre-commit-config.yaml`.
  - GitHub Actions CI running lint, format-check, type-check, and tests on Python 3.11–3.13.
  - `tests/test_smoke.py` verifying the package imports across all layers.
  - Satisfies: NFR-28, NFR-29; spec §15.
- Project documentation set: `README.md`, `PLAN.md` (milestone/batch roadmap), `CLAUDE.md`
  (agent guidance), `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `CONTRIBUTING.md`, and this
  `CHANGELOG.md`. Derived from `mfo_design_notes_spec.md`.

### Notes
- Next up: **batch 0.3 — Persistence layer** (project directory, manifest, SQLite, atomic writes).

<!--
Template for a landed batch:

## [0.x.y] — YYYY-MM-DD
### Batch N.M — <title>
- <what changed>
- Satisfies: <FR-/NFR-/I-/MVP- IDs>
-->
