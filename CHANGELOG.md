# Changelog

All notable changes to mfo are recorded here. Landed **batches** (from [PLAN.md](PLAN.md)) are
moved here when complete, with the spec IDs they satisfied.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches `0.1.0`.

## [Unreleased]

### Added
- **Batch 2.3 — OCR adapter + Japanese (manga-ocr)** (M2 Vision — Detection & OCR):
  - `mfo.vision.ocr`: a swappable `OCREngine` protocol (NFR-17) plus the default `MangaOcrEngine`,
    wrapping `manga-ocr` for offline Japanese recognition incl. vertical text. manga-ocr is an
    **optional** dependency (`pip install 'mfo[ocr]'`) loaded lazily, so importing the vision layer
    never pulls in torch/transformers and the rest of the pipeline runs without it (I-7). Engines
    recognize a region *crop* and return a `RecognizedText` (text + optional confidence/alternatives,
    FR-12/13). A `get_ocr_engine` registry resolves engines by config name; `recognize_file` opens the
    original page read-only (I-1) and crops to the region's source-space bbox (I-2).
  - `mfo.storage.ocr.ocr_regions`: persists one `OCRSpan` per region linked to it, with the
    recognition callable *injected* so storage stays imaging-free (mirrors detect/preprocess). OCR is
    kept separate from translation (FR-15). Each page records an OCR signature folding
    `hash(source, engine-id, regions-fingerprint)`, so re-running skips unchanged pages (NFR-8) and a
    re-detection (changed regions) correctly invalidates the OCR; a (re)OCR'd page has its prior spans
    cleared first, so OCR is idempotent and a forced recompute leaves no stale spans. Adds a
    `Page.ocr` field.
  - `OcrStage` (deps: detect) wired into the pipeline as **opt-in**: since its default engine is an
    optional install, the stage joins `mfo run` only once an engine is chosen via the new `mfo ocr`
    command (`--engine`/`--force`), which persists the choice and reports a clear, actionable error if
    the engine's dependency is missing. `mfo status` already surfaces the span count.
  - Tests: OCR engine registry + unknown-engine error, `recognize_file` cropping/clamping with a spy
    engine, missing-dependency error surfaced clearly; storage persistence + region linkage +
    confidence/alternatives + signature + reopen, idempotent skip, force/engine-change recompute
    without duplicates, re-detection invalidation, region-less page skip; CLI `ocr` config persistence
    + pipeline inclusion, unknown-engine exit, and missing-dependency exit. Adds the optional
    `mfo[ocr]` extra and a mypy override for the stub-less `manga_ocr` module.
  - Satisfies: FR-6, FR-10, FR-12, FR-13, FR-15; MVP-4; I-1, I-2, I-7; NFR-8, NFR-17; spec §10.4.
- **Batch 2.1 — Region detection adapter + baseline** (M2 Vision — Detection & OCR):
  - `mfo.vision.detect`: a swappable `RegionDetector` protocol (NFR-17) plus the default
    `ConnectedComponentsDetector` — a dependency-light OpenCV baseline (Otsu threshold → morphological
    close to merge glyphs → connected-components boxing) that runs CPU-only with **no model download**
    (NFR-21). It returns `DetectedRegion` boxes in source-image pixel space (I-2) with a coarse
    shape-based type guess (bubble/narration/side-text) and a bounded confidence score (I-4). A small
    name→factory registry (`get_detector`) resolves detectors by config name.
  - `mfo.storage.detect.detect_regions`: persists a `Region` per detected box linked to its page, with
    the detection callable *injected* so storage stays imaging-free (mirrors preprocess). Each page
    records a detection signature `hash(source, detector-id)`; re-running skips unchanged pages (NFR-8)
    and a (re)detected page has its prior regions cleared first, so detection is idempotent and a
    forced recompute leaves no stale boxes. Adds `Database.delete` and a `Page.detection` field.
  - `DetectStage` (deps: preprocess) wired into the pipeline; `mfo detect` CLI command
    (`--detector`/`--force`) persists the chosen detector so `mfo run` reproduces it. `mfo status`
    already surfaces the region count.
  - Tests: baseline detection on a synthetic page (shapes → types, reading-order sort, bounded
    confidence, blank/speck rejection), detector registry + unknown-detector error, `detect_file`
    round-trip; storage persistence + page linkage + signature, idempotent skip, force/detector-change
    recompute without duplicates; CLI detect end-to-end, unknown-detector exit, and pipeline inclusion.
    Adds the `opencv-python-headless` dependency.
  - Satisfies: FR-10, FR-11, NFR-17, NFR-21; MVP-3; I-2, I-4; spec §10.3.
- **Batch 1.3 — Resume & project save (consolidation)** (M1 Import & Preprocessing — completes M1):
  - `mfo.cli.stages`: the first concrete pipeline stages — `ImportStage` and `PreprocessStage` —
    wired into the orchestrator from batch 0.5. Each runs through the same idempotent storage/vision
    functions the standalone commands use, so `mfo run` now executes the full `import → preprocess`
    flow (preprocess depends on import). The stages live at the CLI composition root, the one layer
    permitted to depend on both `vision` and `storage`.
  - Project save/reopen for replay (FR-48): `mfo import`/`mfo preprocess` persist their inputs (the
    import source + ordering, the preprocess knobs) into `Project.config`, and `mfo import` records
    the source *before* copying, so a reopened project can rebuild the pipeline and resume. `mfo run`
    assembles the pipeline from that saved config; with no source configured it prints a clear hint.
  - Resume mid-import (FR-5, MVP-10): an import interrupted before its completion record is written
    has no record and re-runs, and `import_pages` skips pages already copied — completed pages are not
    redone. Effective input hashing folds the source listing in, so adding a page re-imports and
    re-preprocesses; an unchanged project is a clean no-op (both stages skip).
  - Tests: CLI end-to-end `run` (import + preprocess, then skip-on-rerun), interrupted-import resume
    (partial pages completed without re-copying the originals, verified by mtime), and added-page
    invalidation. Replaces the empty-pipeline stub test.
  - Satisfies: FR-5, FR-48, MVP-10; NFR-7, NFR-8, NFR-10, NFR-11; I-1, I-5; spec §10.
- **Batch 1.2 — Preprocessing** (M1 Import & Preprocessing):
  - `mfo.vision.preprocess`: pure, storage-free page preprocessing — color-space normalization
    (RGB or `--grayscale`), optional downscale for analysis (`--max-dim`, recording the `scale`
    so coordinates map back to the original — I-2), optional denoise (median filter) and deskew
    (numpy projection-profile angle estimate), and orientation detection. Returns derivative PNG
    bytes plus metadata; the original is read-only (I-1, FR-3).
  - `mfo.storage.preprocess.preprocess_pages`: caches each page's derivative content-addressed by
    `hash(source, config)` and records the metadata on `Page.preprocessing`. The image transform
    is injected, so storage keeps no imaging dependency. Skips pages whose source+config are
    unchanged (NFR-7/8), recomputes on `--force` or config change, and asserts the source is
    byte-identical afterwards (I-1).
  - `mfo preprocess` CLI command (`--grayscale`/`--max-dim`/`--denoise`/`--deskew`/`--force`);
    `mfo status` now reports a preprocess stage line. Adds the `numpy` dependency.
  - Tests: color normalization, grayscale, downscale+scale factor, orientation, denoise, skew
    estimation on a synthetic skewed image, derivative caching + persisted metadata, idempotent
    skip, force/config-change recompute, non-destructive source check, and CLI end-to-end.
  - Satisfies: FR-3, NFR-5, NFR-7, NFR-8; spec §10.2.
- **Batch 1.1 — Directory import & page ordering** (M1 Import & Preprocessing):
  - `mfo.vision.images`: a Pillow-backed image adapter (`read_image_size`, `SUPPORTED_SUFFIXES`
    for PNG/JPG/JPEG/WEBP/TIFF) that raises a clear `ImageError` on unreadable files (NFR-17).
  - `mfo.vision.ingest`: pure, storage-free directory discovery (`discover_images` → `ImportScan`)
    with three ordering strategies (`PageOrder`: natural/numeric `1,2,10`, plain name, and explicit
    manifest order). Malformed images and manifest entries with no matching file are collected as
    skips instead of aborting the import (NFR-9).
  - `mfo.storage.ingest.import_pages`: copies discovered originals into the project's `pages/`
    directory (never moving or modifying source — I-1, FR-3, FR-4) and persists a `Page` per image
    with captured dimensions and a continuing index. Idempotent, so an interrupted import resumes
    without duplicating pages (FR-5).
  - `mfo import <project> <source>` CLI command (`--order`, `--manifest`) wiring discovery →
    import, reporting imported and skipped counts; `mfo status` now shows imported pages.
  - Tests: natural vs. name vs. manifest ordering (incl. `1,2,10`), unsupported-file filtering,
    corrupt-image skip, dimension capture, non-destructive copy, idempotent/resumable import, and
    CLI end-to-end. Adds the `pillow` dependency.
  - Satisfies: FR-1, FR-2, FR-3, FR-4, NFR-9, NFR-17; MVP-1, MVP-2; spec §10.1.
- **Batch 0.5 — Pipeline orchestrator** (M0 Foundation — completes M0):
  - `mfo.core.pipeline`: a dependency-resolved `Pipeline` of `Stage`s. Each stage declares its
    `deps` and a pure `inputs_hash(ctx)`; the orchestrator folds a stage's hash with its
    dependencies' *effective keys* so any upstream change invalidates everything downstream
    (NFR-7/8). Stages run in topological order (duplicate names, unknown deps, and cycles are
    rejected) and communicate only through persisted state, so each is independently restartable.
  - Skip/resume: completed stages are recorded via a `StateStore` keyed by effective input hash;
    re-running skips unchanged stages, and because each record is flushed immediately, an
    interrupted run resumes from where it stopped (I-5, FR-5). `InMemoryStateStore` (core) for
    one-shot/test runs; `JsonStateStore` (storage) persists to `logs/pipeline_state.json` via
    crash-safe atomic writes.
  - Stage selection: `select(only=/from_=/to=)` resolves `--stage`/`--from`/`--to` into the
    ordered set to execute (with full upstream/downstream closure), plus a `--force` override.
    Wired into `mfo run`, which now builds and executes the (still-empty) pipeline; real stages
    register from M1 onward.
  - Tests: dummy 2-stage pipeline ordering, skip-on-rerun, downstream invalidation, force,
    interruption→resume (in-memory and on-disk across simulated process restarts), all selection
    modes, and topology validation (cycle/duplicate/unknown-dep).
  - Satisfies: I-5, FR-5, NFR-7, NFR-8; spec §10, §14.2, §20.
- **Batch 0.4 — CLI skeleton & config** (M0 Foundation):
  - `mfo.cli`: a Typer app (`mfo`) with `init`, `status`, `run`, `export`, `review` commands and
    a `--version`/`--log-level` callback. `init` creates a project (name defaults to the
    directory) and refuses to overwrite an existing one; `status` reports per-stage progress
    (import/detect/ocr/translate/render) inferred from stored data. `run`/`export`/`review`
    open the project and print a placeholder until their milestones land.
  - Layered config (`Settings`, `build_settings`): built-in defaults < TOML config file
    (top-level or `[mfo]` table) < CLI options; unknown keys rejected (FR-47, NFR-12).
  - Idempotent structured logging to stderr (`configure_logging`, `get_logger`).
  - Tests: Typer `CliRunner` coverage of version/help, init (incl. config-file defaults and
    CLI override), status stage reporting, missing-project errors, and the run stub. Adds
    `typer` dependency.
  - Satisfies: FR-46, FR-47, NFR-12; groundwork for FR-45.
- **Batch 0.3 — Persistence layer** (M0 Foundation):
  - `mfo.storage`: project directory layout (`ProjectLayout`, spec §15), human-readable
    `manifest.json` reader/writer (`Manifest`), and a `ProjectStore` facade for
    create/open/save that refuses to overwrite an existing project (I-1).
  - SQLite store (`Database`) with `PRAGMA user_version` migrations and typed, generic entity
    CRUD (`save`/`save_all`/`get`/`list`); each entity is stored as a JSON blob plus indexed
    columns, with `where`/`order_by` validated against known columns to stay injection-safe.
  - Crash-safe `atomic_write_bytes`/`atomic_write_text` (temp + fsync + `os.replace`) and a
    content-addressed `Cache` with SHA-256 hashing helpers (`content_key`, `sha256_file`).
  - Moved the canonical `id` field onto the `MfoModel` base.
  - Tests: atomic-write crash safety, cache round-trip, DB migration/idempotent-reopen,
    entity CRUD round-trip, and ProjectStore create/open/persist.
  - Satisfies: I-1, I-5, FR-4, FR-48, NFR-10, NFR-11, NFR-26, NFR-27; spec §11.2, §15.
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
- **Milestones M0 (Foundation) and M1 (Import & Preprocessing) complete; M2 (Vision) in progress.**
  Detection baseline (2.1) and Japanese OCR (2.3) have landed. Next up: **batch 2.4 — confidence
  surfacing**: aggregate region/OCR confidence, report low-confidence counts in `mfo status`, and
  store flags for downstream highlighting (the optional **batch 2.2 — ML detector adapter** can be
  picked up any time, as it is not on the MVP-critical path).

<!--
Template for a landed batch:

## [0.x.y] — YYYY-MM-DD
### Batch N.M — <title>
- <what changed>
- Satisfies: <FR-/NFR-/I-/MVP- IDs>
-->
