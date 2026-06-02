# Changelog

All notable changes to mfo are recorded here. Landed **batches** (from [PLAN.md](PLAN.md)) are
moved here when complete, with the spec IDs they satisfied.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches `0.1.0`.

## [Unreleased]

### Added
- **Batch 4.2 — Glossary, terminology & style** (M4 Translation):
  - `mfo.core.glossary`: a pure, I/O-free module for terminology consistency. A frozen, serializable
    `GlossaryEntry` pins a source term to a canonical target plus the variant spellings (`aliases`) a
    machine tends to emit. `applicable_entries` finds the entries whose source term occurs in a unit's
    source text; `glossary_terms` renders them for context injection; `apply_glossary` deterministically
    rewrites known aliases to the canonical target in a translation (longest alias first), which is how
    name/term consistency is actually guaranteed on the offline path (FR-23). `entries_from_config` /
    `entries_to_config` round-trip the glossary through `Project.config`.
  - Translation now applies the glossary **two ways** (`mfo.storage.translate.translate_units`): the
    terms applicable to each unit are *injected* into its `context_bundle` under `glossary` so a
    context-aware adapter (M7) can honour them (FR-24, §12.5), and the glossary is *enforced* on the
    machine output deterministically (FR-23). The requested **style** (FR-25) is threaded into each
    `TranslationRequest` (the offline engine can't restyle, but the AI adapters will use it) and recorded
    on the unit (`TranslationUnit.style`). The per-page translation signature now folds the style and the
    injected glossary (via the context), so a glossary edit or style change invalidates the cache and
    re-translates (NFR-8); human/AI candidates and selections are still preserved across recompute (I-3).
  - `TranslationRequest` gains a `style` field (defaulting to `balanced`). `TranslateStage` carries the
    style + glossary, folding both into its `inputs_hash` (bumped to `translate@2`); `build_pipeline`
    rebuilds them from `Project.config`. New `mfo translate --style`; a new `mfo glossary` command group
    (`add` / `list` / `remove`) edits the project glossary, which `mfo translate` and `mfo run` then apply.
  - Tests: core glossary (applicability, alias→canonical normalization, cross-variant consistency,
    inert when term absent, idempotent on canonical, longest-alias-wins, injection payload, config
    round-trip, empty config); storage glossary enforcement on output, injection into the context
    bundle, style threaded to the translator + recorded, and style/glossary cache invalidation; CLI
    style persistence and `glossary add/list/remove` (incl. replace-by-source and unknown-remove exit).
  - Satisfies: FR-23, FR-24, FR-25; SG-2/3/4 groundwork; I-3; NFR-8, NFR-17; spec §10.6, §12.5.
- **Batch 4.1 — Translation adapter + context builder** (M4 Translation; first batch of M4):
  - `mfo.language.translate`: a swappable `Translator` protocol (NFR-17) plus the default
    `ArgosTranslator`, wrapping `Argos Translate` for **offline** neural MT (Tech decision §19), so the
    core path needs no network at run time (NFR-23, I-7). Like manga-ocr, argostranslate is an
    **optional** dependency (`pip install 'mfo[translate]'`) imported lazily, so importing the language
    layer never pulls in the heavy MT stack. Each `TranslationRequest` carries its source text *and*
    its context bundle; the offline engine translates line-by-line and ignores most of the bundle, but
    the protocol passes it through for the AI adapters (M7, §12.5). A `get_translator` registry resolves
    translators by config name.
  - `mfo.core.context.build_context`: a pure, I/O-free builder that folds a unit's neighbouring source
    texts (a configurable window of preceding/following dialogue in reading order) and a page/chapter
    locator into the serializable `context_bundle` (FR-22, NFR-2) — the seam the offline translator and
    later AI adapters read from. The project is modelled as a single volume, so the page index within
    the page count is the chapter locator for now.
  - `mfo.storage.translate.translate_units`: with the translation callable *injected* (so storage stays
    provider-free, mirroring OCR/detect), it assembles each unit's `source_bundle` from its regions' OCR
    spans in reading order — the text grouping deliberately left empty — builds each unit's
    `context_bundle`, translates it, and stores the result as a `TranslationCandidate` on the unit
    (separate from the OCR source, FR-15), establishing the source → OCR → translation link (I-2). Each
    page records a translation signature folding the translator id, target language, and a fingerprint of
    its units (ids, region links, source, context); a re-run skips unchanged pages (NFR-8) and a re-OCR
    or re-grouping invalidates it. A recompute replaces only this stage's own machine (`RAW`) output: any
    human/AI candidate, and a human selection pointing at one, is preserved — automation never silently
    overwrites approved text (I-3). Adds a `Page.translation` field; no migration (it lives in the JSON
    blob, and the `TranslationUnit` candidate fields already existed).
  - `TranslateStage` (deps: group **and** ocr) is wired into the pipeline as **opt-in**: since it
    consumes both the OCR text and the groups, it joins `mfo run` only once both OCR and a translator are
    configured. New `mfo translate` command (`--translator`/`--force`) persists the choice, surfaces a
    clear actionable error if the backend dependency is missing, and reports the unit count; `mfo status`
    already surfaces translated units.
  - Tests: pure context builder (two-sided neighbours, one-sided edges, widened window, empty-neighbour
    drop, page locator, default window); storage source assembly in reading order, context neighbours,
    candidate creation + provenance + reopen, idempotent skip, re-OCR invalidation, forced recompute
    keeping a single machine candidate, human-candidate/selection preservation (I-3), unit-less page
    skip; CLI config persistence + report, unknown-translator exit, pipeline inclusion once OCR is
    configured, and missing-dependency exit. Adds the optional `mfo[translate]` extra and a mypy override
    for the stub-less `argostranslate` module.
  - Satisfies: FR-21, FR-22; MVP-6; I-2, I-3, I-7; NFR-2, NFR-8, NFR-17, NFR-23; spec §10.6, §12.5.
- **Batch 3.2 — Dialogue grouping** (M3 Structure inference):
  - `mfo.core.grouping.group_regions`: a pure heuristic that partitions a page's (reading-ordered)
    regions into conversation chains. Walking regions in reading order, it chains a region onto the
    previous one when they share a region type (SFX never chains) and their edge-to-edge gap is within
    a configurable fraction of their mean height (default 0.4) — rejoining a single utterance split
    across stacked bubbles while leaving distinct utterances separate (FR-19, G-3). Like reading order,
    it is geometry + type only, so it needs no imaging or OCR dependency; it does not mutate its inputs
    and is the seam the review editor (M6) reuses for merge/split.
  - `mfo.storage.grouping.group_into_units`: turns each chain into one `TranslationUnit` carrying the
    ordered region IDs and its `page_id`, establishing the page → unit → region link graph (I-2). The
    unit's `source_bundle` is left empty here — assembling source text from OCR belongs to the
    translation/context stage (M4), keeping grouping independent of (and parallel to) OCR. Each page
    records a grouping signature folding its regions' geometry/type/order and the grouping params; a
    re-run skips unchanged pages (NFR-8) and a re-detection or re-ordering invalidates it. Recomputing
    a page drops its prior units first (idempotent, no orphans); because the skip is signature-driven,
    existing units (and any translations they later carry) are only rebuilt on a real input change or
    `--force`, so automation never silently discards them (I-3). Adds a `Page.grouping` field, a
    `TranslationUnit.page_id` field, and DB migration 002 indexing `translation_units.page_id`.
  - `GroupStage` (deps: structure) is wired into the pipeline and, being geometry-only, is **always
    on** like reading order. New `mfo group` command (`--max-gap`/`--force`) persists the knob and
    reports the unit count; `mfo status` gains a `group` stage line.
  - Tests: core heuristic (close-chains/far-separate, type mismatch, SFX exclusion, transitive
    chaining, reading-order vs input order, configurable threshold, non-mutation); storage unit
    creation + provenance + reopen, idempotent skip, gap-ratio-change recompute, forced-recompute
    replaces (no orphans), re-detection invalidation, region-less page skip; CLI `group` creation +
    status line, config persistence, and `run` including the group stage.
  - Satisfies: FR-11, FR-19; G-3; MVP-5; I-2, I-3; NFR-8; spec §10.5.
- **Batch 3.1 — Reading order** (M3 Structure inference; first batch of M3):
  - `mfo.core.reading_order.order_regions`: a pure, tier-aware manga reading-order heuristic. Regions
    are grouped into horizontal tiers by vertical overlap, tiers are read top-to-bottom, and each tier
    is swept along the reading direction — right-to-left for RTL, left-to-right for LTR (FR-16, FR-17).
    This orders the common multi-panel grid correctly where a naive raster scan would not; tall panels
    spanning tiers are a known hard case deferred to panel detection (batch 3.3). The function is the
    seam the review editor (M6) reuses and that manual correction (FR-20) overrides; it does not mutate
    its inputs.
  - `mfo.storage.reading_order.assign_reading_order`: assigns each region a `reading_order_index` per
    page. Reading order is pure geometry, so — unlike OCR — it needs no imaging dependency and runs on
    the fully offline core path. The index is updated in place so region IDs stay stable (I-2). Each
    page records a structure signature folding the direction and an (order-independent) regions
    fingerprint; re-running skips unchanged pages (NFR-8), and a re-detection invalidates it. A
    **manual reordering survives a plain re-run** — the signature is unchanged so the page is skipped,
    and automation never silently overwrites it (FR-20, I-3) — and is only re-derived on `--force`.
    Adds a `Page.structure` field.
  - `StructureStage` (deps: detect) is wired into the pipeline and, being geometry-only, is **always
    on** (no optional install), unlike OCR. Its direction defaults to the project's reading direction.
    New `mfo order` command (`--direction`/`--force`) persists the choice and reports the count;
    `mfo status` gains an `order` stage line.
  - Tests: core heuristic (RTL/LTR grid ordering, default direction, misaligned-top tier grouping,
    non-mutation); storage index assignment + provenance + reopen, idempotent skip, direction-change
    recompute, stable IDs, re-detection invalidation, manual-order-survives-rerun/force-overrides,
    region-less page skip; CLI `order` assignment + status line, direction config persistence, and
    `run` including the structure stage.
  - Satisfies: FR-16, FR-17, FR-20 (data hook); MVP-5; I-2, I-3; NFR-8; spec §10.5.
- **Batch 2.4 — Confidence surfacing** (M2 Vision — Detection & OCR; completes M2's MVP scope):
  - `mfo.core.confidence`: pure aggregation that combines a region's detection and OCR confidence
    into one conservative score — the *weakest* signal (`min` of known values), so a confidently
    detected but poorly-read region still surfaces. Unknown confidence (manga-ocr reports none)
    is ignored in the aggregate but treated as low downstream, keeping genuine uncertainty visible
    rather than hidden (I-4). `is_low_confidence` compares against a tunable threshold (default 0.5).
  - `mfo.storage.confidence`: applies that logic across a project — `region_confidences` /
    `low_confidence_regions` make the review set **queryable** (MVP-11), `confidence_report`
    summarizes totals/scored/low/flagged for reporting, and `flag_low_confidence` persists the
    verdict by marking low-confidence regions `NEEDS_REVIEW`. It only touches `AUTO` regions, so a
    human's status decision is never overwritten (I-3), and is idempotent.
  - `mfo status` now prints a Confidence section (low-confidence count vs. total at the threshold,
    plus how many are flagged), with a `--threshold` option (NFR-4). New `mfo flag` command persists
    the review flags for the downstream editor, also `--threshold`-tunable.
  - Tests: core aggregation (weakest-signal, unknown handling, threshold/None semantics); storage
    querying, OCR-confidence pulling a region below threshold, report counts, AUTO-only flagging
    (I-3) and idempotency; CLI status reporting, `flag` persistence + threshold.
  - Satisfies: I-4, FR-12, NFR-4; MVP-11; spec §10.3/§10.4.
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
- **Milestones M0 (Foundation), M1 (Import & Preprocessing), and M2 (Vision — Detection & OCR)
  complete; M3 (Structure inference) MVP-complete; M4 (Translation) started.** M2's MVP scope landed
  across 2.1 (detection), 2.3 (Japanese OCR), and 2.4 (confidence surfacing); the optional **batch 2.2
  — ML detector adapter** can be picked up any time, as it is not on the MVP-critical path. M3 landed
  3.1 (reading order) and 3.2 (dialogue grouping), satisfying MVP-5; the optional **batch 3.3 — panel
  detection** (best-effort panel boundaries to refine reading order, or cleanly disabled) remains and
  is off the MVP-critical path. M4 landed 4.1 (translation adapter + context builder) and 4.2 (glossary,
  terminology & style). Next up: **batch 4.3 — Traceability & mapping export** (selected translation per
  unit, full source→OCR→translation link graph, JSON mapping export via `mfo export --mapping`, and
  `EditRecord` scaffolding; MVP-7), then the optional 4.4 (API translation adapter).

<!--
Template for a landed batch:

## [0.x.y] — YYYY-MM-DD
### Batch N.M — <title>
- <what changed>
- Satisfies: <FR-/NFR-/I-/MVP- IDs>
-->
