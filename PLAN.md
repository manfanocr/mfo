# mfo — Implementation Plan

This plan turns [mfo_design_notes_spec.md](mfo_design_notes_spec.md) into concrete,
ordered, testable work. It is the **single source of truth for sequencing**.

- Work is grouped into **Milestones** (coherent capability) → **Batches** (one PR-sized unit).
- Each batch lists **scope**, the **spec items it satisfies**, and a **Definition of Done (DoD)**.
- When a batch lands, move its entry to [CHANGELOG.md](CHANGELOG.md) and check it off here.
- Batches are sized so each is independently reviewable and leaves `main` green.

**Cross-references** use the spec's IDs: goals `G-*`, functional `FR-*`, non-functional
`NFR-*`, invariants `I-*`, MVP `MVP-*`, stretch `SG-*`.

---

## Guiding engineering rules (apply to every batch)

These are non-negotiable and derive from the spec invariants:

1. **Stage outputs are serializable and cached** (I-5, NFR-7/8). A stage reads its inputs
   from disk/DB and writes its outputs back; re-running with unchanged inputs is a no-op.
2. **Never destroy source** (I-1, FR-3). Originals are read-only; all derived data lives in
   the project dir.
3. **Everything is traceable** (I-2, I-6, FR-41/42). Stable IDs flow source → OCR →
   translation → render.
4. **Human edits win** (I-3, FR-29). Automated stages must detect and preserve user edits.
5. **AI is optional** (I-7, NFR-23/24). The core path must run fully offline.
6. **Adapters, not hard-coded providers** (NFR-17, §14.3). OCR/detect/translate/render are
   pluggable behind interfaces.
7. **Tests cover critical stages** (NFR-29). Each batch ships with tests.

---

## Tech decisions (resolving spec §19 open questions)

These are the working defaults; revisit if a milestone disproves them. Recorded so
contributors don't re-litigate.

| Question (§19) | Decision | Rationale |
|---|---|---|
| OCR backend | **manga-ocr** (JP) as first adapter; Tesseract/PaddleOCR adapters later | Best JP manga accuracy, offline, MIT/Apache friendly |
| UI: native vs browser | **Local web app** (FastAPI backend + lightweight SPA), launched via `mfo review` | Cross-platform (NFR-20) with one codebase; native shell (Tauri/PySide) is a later option |
| Translation plugin-driven? | **Yes, from day one** | Required by NFR-17; core ships an offline adapter (Argos/NLLB) + an API adapter |
| Panel analysis depth | **Minimal for MVP** (reading-order heuristics, not full panel graphs); panel detection is a later batch | MVP-5 needs order, not deep understanding (NG-2) |
| Visual fidelity vs readability | **Readability-first** with fidelity as best-effort | Matches NFR-2/3; exact cleanup is user-guided |
| Auto text removal vs guided | **Best-effort auto mask + always-editable** | FR-31/32 with I-3 |

**Stack:** Python ≥3.11 · Pillow + OpenCV + NumPy · SQLite (relational) + JSON manifest
(human-readable) · Typer (CLI) · FastAPI (review UI) · pytest + ruff + mypy · `uv`/pip with
`pyproject.toml`.

---

## Milestone overview

| # | Milestone | Delivers | Key MVP items |
|---|-----------|----------|---------------|
| M0 | Foundation | Repo, tooling, data model, persistence, CLI skeleton | — |
| M1 | Import & Preprocess | Folder → ordered, normalized project | MVP-1, MVP-2, MVP-10 |
| M2 | Vision (Detect + OCR) | Regions + Japanese OCR with confidence | MVP-3, MVP-4, MVP-11 |
| M3 | Structure | Reading order + dialogue grouping | MVP-5 |
| M4 | Translation | Context-aware bulk translation + mapping | MVP-6, MVP-7 |
| M5 | Render & Export | Masking, typesetting, page export | MVP-9 |
| M6 | Review Editor | Local web in-place editor | MVP-8 |
| M7 | AI Refinement | Optional assist/review/auto modes | — (post-MVP) |
| M8 | Hardening & Stretch | Perf, plugins, stretch goals | SG-* |

**MVP = M0–M6 complete.** That satisfies the spec's Definition of Done (§21).

---

## M0 — Foundation

Goal: a skeleton that compiles, tests, lints, and defines the contracts everything else
plugs into.

### Batch 0.1 — Repo scaffolding & tooling ✅ *(landed — see CHANGELOG)*
- **Scope:** `pyproject.toml` (package `mfo`), `src/mfo/{core,vision,language,render,ui,storage,cli}`
  layout, ruff + mypy + pytest config, pre-commit, GitHub Actions CI (lint + test on 3.11–3.13),
  `.gitignore`, `.editorconfig`.
- **Satisfies:** NFR-28, NFR-29; §15 file structure.
- **DoD:** `pip install -e ".[dev]"`, `ruff check`, `mypy src`, `pytest` all pass on an empty
  test. CI green.

### Batch 0.2 — Core data model ✅ *(landed — see CHANGELOG)*
- **Scope:** Dataclasses/Pydantic models for `Project, Page, Region, OCRSpan, TranslationUnit,
  EditRecord, RenderArtifact` (§11). Stable ID scheme (ULID/UUID). Enums for region type,
  reading direction, region status. JSON (de)serialization round-trips.
- **Satisfies:** I-2, FR-41, NFR-30; §11.
- **DoD:** Models serialize/deserialize losslessly; property tests for ID stability and
  round-trip. No I/O yet.

### Batch 0.3 — Persistence layer ✅ *(landed — see CHANGELOG)*
- **Scope:** `storage/` — project directory layout (§15), `manifest.json` writer/reader,
  SQLite schema + migrations for relational data, cache directory abstraction with content
  hashing, atomic writes (crash-safe).
- **Satisfies:** I-1, I-5, FR-4, FR-48, NFR-10/11, NFR-26/27.
- **DoD:** Create/open/save a project; survives simulated crash mid-write (atomic temp+rename);
  schema migration test.

### Batch 0.4 — CLI skeleton & config ✅ *(landed — see CHANGELOG)*
- **Scope:** Typer app: `mfo init`, `mfo run`, `mfo status`, `mfo export`, `mfo review`
  (stubs). Config loading (file + CLI override, §FR-47), structured logging, project resolution.
- **Satisfies:** FR-46, FR-47, NFR-12; §FR-45 headless groundwork.
- **DoD:** `mfo init <dir>` creates a valid empty project; `mfo status` reports stage state;
  `--help` documented.

### Batch 0.5 — Pipeline orchestrator ✅ *(landed — see CHANGELOG)*
- **Scope:** Stage interface (`Stage.run(ctx) -> result`), dependency/ordering, per-stage cache
  invalidation by input hash, `--stage`/`--from`/`--to` selection, resume support.
- **Satisfies:** I-5, FR-5, NFR-7/8; §10 pipeline contract; implementation note §20.
- **DoD:** A dummy 2-stage pipeline runs, caches, skips unchanged stages, resumes after
  interruption. Each stage independently restartable.

---

## M1 — Import & Preprocessing  (MVP-1, MVP-2, MVP-10)

### Batch 1.1 — Directory import & page ordering ✅ *(landed — see CHANGELOG)*
- **Scope:** Scan dir for PNG/JPG/WEBP/TIFF, build `Page` entries, ordering strategies
  (filename, natural/numeric sort, manifest override), dimension/metadata capture, graceful
  handling of malformed images.
- **Satisfies:** FR-1, FR-2, NFR-9; MVP-1, MVP-2; §10.1.
- **DoD:** Import a sample folder; ordering strategies tested incl. `1,2,10` natural sort;
  corrupt file skipped with clear warning.

### Batch 1.2 — Preprocessing ✅ *(landed — see CHANGELOG)*
- **Scope:** Normalize color space, optional downscale for analysis (keep original for render),
  optional deskew/denoise (off by default), page orientation detection. Store preprocessing
  metadata on `Page`.
- **Satisfies:** FR-3 (non-destructive), NFR-5; §10.2.
- **DoD:** Preprocessed derivatives written to cache; originals untouched (hash check);
  metadata persisted.

### Batch 1.3 — Resume & project save (consolidation) ✅ *(landed — see CHANGELOG)*
- **Scope:** End-to-end `init → import → preprocess`, save/reopen, resume mid-import.
- **Satisfies:** FR-5, FR-48, MVP-10; NFR-10/11.
- **DoD:** Kill during import, reopen, `mfo run` resumes without redoing completed pages.

---

## M2 — Vision: Detection & OCR  (MVP-3, MVP-4, MVP-11)

### Batch 2.1 — Region detection adapter + baseline ✅ *(landed — see CHANGELOG)*
- **Scope:** `RegionDetector` adapter interface; a dependency-light OpenCV baseline detector
  (connected-components / bubble heuristics) so the project works with **no model download**;
  outputs `Region` (bbox/polygon, type guess, confidence).
- **Satisfies:** FR-10, FR-11, NFR-17/21; MVP-3; §10.3.
- **DoD:** Detects regions on sample pages; confidence stored; adapter swappable via config.

### Batch 2.2 — ML detector adapter (optional) ✅ *(landed — see CHANGELOG)*
- **Scope:** Adapter for a trained bubble/text detector (e.g. comic-text-detector / YOLO),
  lazy model download, CPU + optional GPU, region-type classification (bubble/narration/SFX/caption).
- **Satisfies:** FR-11, FR-14 (best-effort), NFR-22; SG-5 groundwork.
- **DoD:** Optional install path; falls back to baseline if model absent; classifies types.

### Batch 2.3 — OCR adapter + Japanese (manga-ocr) ✅ *(landed — see CHANGELOG)*
- **Scope:** `OCREngine` adapter interface; manga-ocr adapter (JP, vertical text), per-region
  `OCRSpan` with confidence + alternatives where available; OCR stored separately from
  translation.
- **Satisfies:** FR-6, FR-10, FR-12, FR-13, FR-15; MVP-4; §10.4.
- **DoD:** OCRs detected regions on JP sample; confidence + (when available) alternates stored;
  vertical text handled.

### Batch 2.4 — Confidence surfacing ✅ *(landed — see CHANGELOG)*
- **Scope:** Aggregate region/OCR confidence; `mfo status` shows low-confidence counts; flag
  store for downstream highlighting.
- **Satisfies:** I-4, FR-12, NFR-4; MVP-11.
- **DoD:** Low-confidence regions queryable and reported.

---

## M3 — Structure Inference  (MVP-5)

### Batch 3.1 — Reading order ✅ *(landed — see CHANGELOG)*
- **Scope:** Manga reading-order heuristic (RTL, top-to-bottom, tier-aware), configurable
  direction (FR-17), per-region `reading_order_index`. Manual-override hook for M6.
- **Satisfies:** FR-16, FR-17, FR-20 (data hook); MVP-5; §10.5.
- **DoD:** Correct order on RTL sample pages; LTR/TTB toggle tested.

### Batch 3.2 — Dialogue grouping ✅ *(landed — see CHANGELOG)*
- **Scope:** Group regions into `TranslationUnit`s (proximity, type, order); ordered region
  IDs preserved; conversation-chain heuristic.
- **Satisfies:** FR-11, FR-19; G-3; §10.5.
- **DoD:** Units formed and persisted with ordered region refs; tested on sample.

### Batch 3.3 — Panel detection (optional, light) ✅ *(landed — see CHANGELOG)*
- **Scope:** Best-effort panel boundary detection to refine reading order; off if not helpful.
- **Satisfies:** FR-18; SG-1 groundwork.
- **DoD:** Panel-aware ordering improves a known tricky sample, or is cleanly disabled.

---

## M4 — Translation  (MVP-6, MVP-7)

### Batch 4.1 — Translation adapter + context builder ✅ *(landed — see CHANGELOG)*
- **Scope:** `Translator` adapter interface; context bundle (nearby regions, page, chapter);
  offline adapter (Argos Translate / NLLB via CTranslate2) as default; batch translation of
  linked units.
- **Satisfies:** FR-21, FR-22, NFR-2, NFR-17, NFR-23; MVP-6; §10.6, §12.5.
- **DoD:** Translates units with context offline; results stored as candidates per unit.

### Batch 4.2 — Glossary, terminology & style ✅ *(landed — see CHANGELOG)*
- **Scope:** Glossary injection, name/honorific/terminology consistency, style options
  (literal/balanced/natural/localized).
- **Satisfies:** FR-23, FR-24, FR-25; SG-2/3/4 groundwork; §12.5.
- **DoD:** Glossary terms enforced; style toggle changes output; consistency test.

### Batch 4.3 — Traceability & mapping export ✅ *(landed — see CHANGELOG)*
- **Scope:** Selected translation per unit, full link graph source→OCR→translation, JSON export
  of mappings; `EditRecord` scaffolding.
- **Satisfies:** I-2, I-6, FR-26 (data), FR-41/42/43; MVP-7; §21.
- **DoD:** `mfo export --mapping` emits JSON tracing every output region to its source.

### Batch 4.4 — API translation adapter (optional) ✅ *(landed — see CHANGELOG)*
- **Scope:** Opt-in cloud/LLM translation adapter behind explicit config; never default.
- **Satisfies:** NFR-24/25, §14.3; FR-21.
- **DoD:** Works when configured; core remains fully offline without it.

---

## M5 — Rendering & Export  (MVP-9)

### Batch 5.1 — Text masking / removal ✅ *(landed — see CHANGELOG)*
- **Scope:** Mask original text within regions; best-effort background reconstruction (inpaint);
  preserve line art; always reversible (keep originals).
- **Satisfies:** FR-31, FR-32, FR-33, I-1, I-6; §10.8.
- **DoD:** Masked layer produced; original recoverable; line-art preservation sanity test.

### Batch 5.2 — Font fitting & placement ✅ *(landed — see CHANGELOG)*
- **Scope:** Text wrap/scale/align within bbox, font selection, stroke/outline, style presets,
  bubble-aware fitting.
- **Satisfies:** FR-34, FR-35, NFR-3; SG-6 groundwork; §10.8.
- **DoD:** Text fits sample bubbles without overflow; presets applied; deterministic output.

### Batch 5.3 — Composite & export pages ✅ *(landed — see CHANGELOG)*
- **Scope:** Render translated text onto (masked) page, export images + project records +
  optional transcript/manifest.
- **Satisfies:** FR-14, FR-43, MVP-9, NFR-26; §7.6, §10.8.
- **DoD:** `mfo export` produces translated pages + JSON mapping for the sample volume.

---

## M6 — Review Editor  (MVP-8)  → completes MVP

### Batch 6.1 — Review backend/API ✅ *(landed — see CHANGELOG)*
- **Scope:** FastAPI service exposing pages, regions, OCR, translations, confidence, edit
  history; mutation endpoints that write `EditRecord`s; precedence of edits over automation.
- **Satisfies:** I-3, FR-37, FR-42, FR-49; §13.2.
- **DoD:** API serves and mutates project state; edits persisted as records.

### Batch 6.2 — Local web editor UI ✅ *(landed — see CHANGELOG)*
- **Scope:** `mfo review` launches local app: image canvas, clickable regions, side panel
  (OCR/translation/history/confidence), keyboard navigation, zoom/pan, dark mode.
- **Satisfies:** FR-36, NFR-13/14/15/16; §13.1–13.5.
- **DoD:** Open a project, click regions, see all data, navigate by keyboard.

### Batch 6.3 — In-place editing & region ops ✅ *(landed — see CHANGELOG)*
- **Scope:** Edit translation in place, adjust font/breaks/align/position, split/merge regions,
  status flags (correct/needs-review/ignore/manual), re-render preview, low-confidence-first
  review queue, manual reading-order correction.
- **Satisfies:** FR-20, FR-37, FR-38, FR-39, FR-40, FR-26; §13.3/13.4.
- **DoD:** All region ops persist and re-render; review queue surfaces low-confidence first.

---

## M7 — AI-Assisted Refinement (post-MVP)

### Batch 7.1 — AI assist adapter ✅ *(landed 2026-06-03)*
- **Scope:** Optional LLM adapter producing candidate + literal + readability rewrite +
  confidence + rationale + warnings; bubble-fit shortening; speaker-shift hints.
- **Satisfies:** FR-27, FR-28, FR-30; §12.1/12.3/12.5.
- **DoD:** Produces structured suggestions; disabled by default; offline core unaffected.
- **Shipped:** `mfo.language.assist` (`AiAssistant`/`LlmAssistant`, `AssistRequest`,
  `AssistSuggestion`, `get_assistant`); env-only `MFO_AI_*` config (falls back to `MFO_API_*`);
  defensive JSON parsing; full offline test coverage. See CHANGELOG.

### Batch 7.2 — AI modes ✅ *(landed 2026-06-03)*
- **Scope:** Assist / Review / Auto modes (§12.4); auto applies only high-confidence and keeps
  full audit trail; never overwrites approved text.
- **Satisfies:** I-3, I-7, FR-29; §12.4.
- **DoD:** Each mode behaves per spec; auto-applied changes are auditable and reversible.
- **Shipped:** `AssistMode` enum; `mfo.storage.assist.assist_units` (attaches AI candidates,
  resolves selection per mode, records audit edits, page-level cache); `mfo assist` CLI command
  (`--mode/--assistant/--min-confidence/--style`, off the default `run`); `save_assist_config`;
  USER_GUIDE section. See CHANGELOG.

### Batch 7.3 — Confidence-driven review integration ✅ *(landed 2026-06-03)*
- **Scope:** Wire AI confidence + uncertainty into the M6 review queue and flags.
- **Satisfies:** I-4, FR-30, NFR-4.
- **DoD:** AI-flagged regions appear in the review queue with rationale.
- **Shipped:** `mfo.core.confidence.ai_candidate`; the review backend now folds AI uncertainty into
  `review_queue` (AI-flagged regions sort up beside low-confidence ones, carrying the AI candidate's
  confidence + rationale), `project_summary` (per-page `ai_flagged` count), and `page_view` region
  payloads (`ai_flagged`/`ai_confidence`/`ai_rationale`). Editor surfaces it: an **AI** badge +
  rationale tooltip on queue rows, a violet ring on flagged regions, and the rationale on the
  candidate card. Off the core path — no AI candidate means no flag (I-7). See CHANGELOG.

---

## M8 — Hardening & Stretch (post-MVP)

Everything here is **post-MVP and optional**. Unlike M0–M6 (a strict sequence) these batches are
largely **independent and à la carte** — pick whichever delivers value next; only the few
dependencies noted below force an order. Each still ships its own tests and leaves `main` green.
The first three are *hardening* (NFR-driven); the rest realize the spec's stretch goals (SG-1…SG-10,
§17). Each builds on a seam that already exists, so none is a rewrite.

**Suggested order & dependencies:** 8.1–8.3 (hardening, independent, low-risk) → 8.3 *before* the
adapter-adding stretch batches (8.7, 8.9) so new providers register through one mechanism → 8.4
needs panel detection (3.3, landed) → 8.5 *before* 8.6 (presets bundle a shared glossary) → 8.8
needs polygons on `Region` (present) → 8.10 needs the edit log + history (M6/B4, landed) → 8.11 last.

### Batch 8.0 — Fused detect+recognize for det+rec engines (PaddleOCR) ✅ *(landed 2026-06-03)*
- **Scope:** PaddleOCR runs detection **and** recognition in one model pass, yet today the `paddle`
  detector (standalone `TextDetection`) discards the text and `mfo ocr --engine paddleocr` re-runs
  paddle per region — paddle's recognition work happens twice. Add a fused detector `paddle-rec`
  that runs the full pipeline once and captures the recognized text + per-box confidence onto each
  `DetectedRegion` (new optional `text`/`text_confidence`). The detect stage persists those as
  provisional, provenance-tagged `OCRSpan`s (new `OCRSpan.source`, I-2) and records that the page was
  recognized; the OCR stage **adopts** them instead of re-recognizing when present
  (`mfo ocr --reuse-detection`, default on), filling only regions that lack detection text. Keeps
  detect and OCR as separate, restartable, separately-cached stages (I-5) — `--no-reuse-detection`
  /`--force` (or picking a different `--engine`) still does a fresh OCR pass, so `--engine` stays
  authoritative. As a bonus the fused path surfaces paddle's real per-box scores instead of the
  current `0.9` detection placeholder (I-4).
- **Satisfies:** NFR-7, NFR-8, NFR-17; FR-12, FR-13; I-2, I-4, I-5.
- **DoD:** After `mfo detect --detector paddle-rec`, `mfo ocr` reuses the detection text with **no**
  second paddle pass and shows real per-box confidence; `--no-reuse-detection` forces fresh OCR;
  baseline/ML detection and the manga-ocr path are unchanged. Tested with fakes (no paddle install).
- **Shipped:** `paddle-rec` detector (`PaddleRecDetector`, full pipeline → `DetectedRegion.text`/
  `text_confidence`); `OCRSpan.source` provenance; detect stage persists provisional spans +
  `detection.recognized`; `ocr_regions(reuse_detection=True)` adopts them (only OCRs gaps), with
  `mfo ocr --reuse-detection/--no-reuse-detection`; `get_detector(..., lang=)`; USER_GUIDE +
  CHANGELOG. See CHANGELOG.

### Batch 8.1 — Parallel processing & performance tuning ✅ *(landed 2026-06-03)*
- **Scope:** Process pages concurrently across the heavy stages (detect/OCR/translate/render) with a
  configurable worker count (`--jobs`); a small profiling/benchmark harness; cache-key audit so
  parallelism never corrupts or races the per-input cache. Keep determinism (stable IDs, ordered
  output) regardless of worker count.
- **Satisfies:** NFR-5, NFR-6, NFR-7, NFR-8; §20.
- **DoD:** A multi-page volume processes pages in parallel with a measurable speedup; results are
  byte-identical to the serial run; cache still skips unchanged pages. Benchmark documented.

### Batch 8.2 — Archive import (CBZ/ZIP) ✅ *(landed 2026-06-03)*
- **Scope:** Extend the import adapter (`mfo.vision.ingest` / `mfo import`) to read `.cbz`/`.zip`
  (and plain folders, as today). Pages are extracted **read-only** into the project cache (originals
  untouched, I-1), ordered by the same natural-sort strategy. CBR/RAR noted as out of scope (needs a
  non-free dependency).
- **Satisfies:** FR-1, FR-2, I-1, NFR-9; relaxes NG-4.
- **DoD:** `mfo import proj vol.cbz` builds an ordered project; corrupt entries skipped with a clear
  warning; source archive never modified.
- **Shipped:** `is_archive`/`ARCHIVE_SUFFIXES`/`extract_archive` in `mfo.vision.ingest`;
  `discover_images(..., extract_to=)` stages a CBZ/ZIP into the project cache then discovers it like
  a directory (basename-flattened, zip-slip-safe; ignores `ComicInfo.xml`/resource forks; skips
  corrupt entries + duplicate names; raises on a wholly unreadable archive). `mfo import` accepts a
  folder or archive; `ImportStage` replays archive imports under `mfo run` (cache extract dir +
  size/mtime in `inputs_hash`). USER_GUIDE + CHANGELOG. See CHANGELOG.

### Batch 8.3 — Formal plugin system (entry-point discovery) ✅ *(landed 2026-06-04)*
- **Scope:** Promote the per-layer `_FACTORIES` registries (detect/OCR/translate/assist/render) to a
  documented extension API discoverable via Python **entry points** (`mfo.detectors`, `mfo.ocr`,
  `mfo.translators`, `mfo.assistants`, `mfo.renderers`), so a third-party package registers an
  adapter without editing mfo. `get_*` resolvers consult built-ins then entry points. A contributor
  doc (`docs/PLUGINS.md`) is the "marketplace" groundwork (a curated index is just a list of these).
- **Satisfies:** NFR-17, NFR-19, SG-9; §14.3.
- **DoD:** A sample out-of-tree package registers a detector via entry points and `mfo detect
  --detector <it>` finds and runs it; offline built-ins still resolve with no plugins installed.
- **Shipped:** `mfo.core.plugins` (`discover_plugins`, `resolve_factory`, the five group-name
  constants); `get_detector`/`get_ocr_engine`/`get_translator`/`get_assistant` resolve built-ins
  first then their entry-point group (built-ins win and can't be shadowed; a broken plugin is
  skipped with a warning, never fatal — NFR-9). `mfo.renderers` group reserved (render isn't
  adapter-pluggable yet). `docs/PLUGINS.md` contributor guide + ARCHITECTURE/README pointers. See
  CHANGELOG.

### Batch 8.4 — Panel-aware context (SG-1) ✅ *(landed 2026-06-04)*
- **Scope:** Use the landed panel detection (3.3) to scope the translation context window to the
  current panel — neighbor selection in `mfo.core.context.build_context` prefers same-panel units and
  records panel grouping in the context bundle, giving the `api`/AI path tighter, more relevant
  context without merging units (one bubble = one unit stays).
- **Satisfies:** SG-1; FR-18, FR-22; §12.5.
- **DoD:** On a multi-panel sample the context bundle reflects panel boundaries; an A/B shows context
  no longer bleeds across panels; offline adapters (which ignore context) are unaffected.
- **Shipped:** `Region.panel_index` stamped by the panel-aware reading-order stage (`panel_of`
  exposed from `mfo.core.reading_order`; cleared on the flat path); `build_context(..., panels=)`
  scopes the neighbour window to a unit's panel and records its `panel` in the bundle (out-of-panel
  units keep the plain window); `translate_units` derives each unit's panel from its lead region and
  passes it through — flat projects keep a byte-identical bundle (no spurious re-translate). Offline
  adapters ignore context, so they're unaffected (I-7). USER_GUIDE note. See CHANGELOG.

### Batch 8.5 — Cross-volume name & terminology memory (SG-2, SG-3) ✅ *(landed 2026-06-04)*
- **Scope:** A **series-level** shared glossary/terminology store (above the per-project glossary of
  4.2) that persists character names, honorifics, and pinned terms across volumes, with
  export/import for team sharing. New units consult project → series glossary; the review editor can
  promote a project term to the series store.
- **Satisfies:** SG-2, SG-3; FR-23, FR-24, FR-25; I-2.
- **DoD:** A name fixed in volume 1 is enforced in volume 2 via the shared store; the store
  exports/imports losslessly (round-trip test); precedence (project overrides series) is tested.
- **Shipped:** `mfo.core.series` (`SeriesGlossary` model + `upsert_entry`/`remove_entry`/
  `merge_entries`) and `mfo.core.glossary.merge_glossaries` (project-over-series precedence);
  `mfo.storage.series` (atomic, versioned JSON store that doubles as the portable export — load/save
  round-trip losslessly). A volume links a shared store via `Project.config["series_glossary"]`;
  `load_effective_glossary` merges project → series and feeds `mfo translate`, `mfo run`, and the UI
  retranslate. CLI: `mfo glossary series link/list/remove/export/import` + `mfo glossary promote`;
  review API `POST /api/glossary/series/promote`. Unlinked projects are unchanged (offline core
  unaffected, I-7). USER_GUIDE + DATA_MODEL. See CHANGELOG.

### Batch 8.6 — Per-series style presets (SG-4) ✅ *(landed 2026-06-04)*
- **Scope:** Named, series-scoped presets bundling translation style (4.2), the shared glossary
  (8.5), and render presets (5.2), selectable when creating/configuring a project. *(Depends on 8.5
  for the glossary half.)*
- **Satisfies:** SG-4; FR-25, FR-35; §12.5.
- **DoD:** Applying a series preset to a new project sets its style, glossary, and render config in
  one step; presets persist and are listed by name.
- **Shipped:** `mfo.core.presets` (`SeriesPreset` bundling style + series-glossary link +
  `RenderPreset` masking knobs; `SeriesPresetStore` + `upsert_preset`/`remove_preset`/`find_preset`/
  `series_preset_names`); `mfo.storage.presets` (atomic, versioned JSON store outside the project that
  doubles as the portable export — load/save round-trip losslessly); `apply_series_preset` (CLI) sets
  style (preserving the translator), links the glossary, and persists render config in one step. CLI:
  `mfo preset save/list/remove/apply`. Project-config wiring only, so unlinked projects are unchanged
  (offline core unaffected, I-7); no schema migration. USER_GUIDE + DATA_MODEL. See CHANGELOG.

### Batch 8.7 — SFX detection & transliteration (SG-5) ✅ *(landed 2026-06-04)*
- **Scope:** Classify SFX regions (the `RegionType.SFX` already exists) and handle them distinctly
  from dialogue: an optional transliteration/translation path producing an SFX-specific candidate, a
  per-project toggle to render, transliterate, or leave SFX untouched. A new detector/classifier
  adapter registers via 8.3.
- **Satisfies:** SG-5; FR-11, FR-14; NFR-17.
- **DoD:** SFX regions are typed and get a transliteration candidate; the render toggle is honored on
  a sample; dialogue handling is unchanged.
- **Shipped:** `mfo.vision.sfx` (pluggable `SfxClassifier` + offline `HeuristicSfxClassifier`, new
  `mfo.sfx_classifiers` group; promotes only `UNKNOWN`/auto regions — I-3); `mfo.language.transliterate`
  (pluggable `Transliterator` + offline `KanaTransliterator`, new `mfo.transliterators` group);
  `CandidateKind.SFX` + `SfxMode` (render/transliterate/skip); `mfo.storage.sfx.process_sfx`
  (classify + attach/select SFX candidate, never over a human choice); render `skip_types` filter on
  `mask_pages`/`page_placements`/`composite_pages`; `mfo sfx` CLI + `SfxStage` wired into `mfo run`
  (after group+OCR; mask depends on it in skip mode). Offline defaults, so the core path is
  unchanged for non-SFX projects (I-7). USER_GUIDE + DATA_MODEL. See CHANGELOG.

### Batch 8.8 — Bubble-shape-aware text wrapping (SG-6) ✅ *(landed 2026-06-04)*
- **Scope:** When a region carries a polygon (the `Region.polygon` field exists), wrap and fit text
  to the **bubble shape** rather than its bounding box in the render font-fitter (5.2), reducing
  text that visually spills outside round/irregular bubbles.
- **Satisfies:** SG-6; FR-34, FR-35, NFR-3.
- **DoD:** Text fits inside a non-rectangular polygon sample without crossing the bubble outline;
  rectangular regions render identically to today (no regression).
- **Shipped:** `mfo.render.shape` (`scanline_span`/`band_inner` — the polygon's interior width at a
  line's vertical band); `fit_text(..., polygon=)` follows the bubble shape (per-line variable-width
  wrap, vertically-centred, line count settled over bounded passes) and records per-line `line_bands`
  + `y_start` on `TextLayout`; `render_layout` aligns each line within its own band. `Placement`/
  `PagePlacement` carry the polygon; `page_placements` passes a single-region unit's `Region.polygon`
  through (chained units stay box-fit), folded into the render cache signature. No polygon → the box
  path is untouched and byte-identical (no regression). USER_GUIDE + DATA_MODEL. See CHANGELOG.

### Batch 8.9 — LLM OCR correction (SG-7) ✅ *(landed 2026-06-04)*
- **Scope:** An **opt-in** assist path (built on the M7 AI layer) that proposes corrections for
  low-confidence OCR, surfaced as `OCRSpan.alternatives` / review suggestions — never overwriting the
  recognized text (I-3). Off the core path; text-only, no page image (NFR-25).
- **Satisfies:** SG-7; FR-12, FR-13, FR-30; I-3, I-7.
- **DoD:** A low-confidence span gets LLM-suggested alternatives visible in review and acceptable
  with one click; disabled by default; offline OCR unaffected.
- **Shipped:** `mfo.language.ocr_correct` (`OcrCorrector` adapter + `LlmOcrCorrector` over the shared
  AI transport/config, new `mfo.ocr_correctors` group; text-only, env-only, no offline default);
  `mfo.storage.correct_ocr.correct_ocr_spans` appends proposed readings to low-confidence spans'
  `alternatives` (never the text — I-3; page-cached, idempotent/dedup); `Page.ocr_correction`
  provenance. CLI `mfo ocr-correct` (off `mfo run`); review `accept_ocr_alternative` + `POST
  /api/ocr/{id}/accept` and a one-click **Use** button per alternative in the editor. Disabled by
  default; a project that never runs it is unaffected (I-7). USER_GUIDE + DATA_MODEL. See CHANGELOG.

### Batch 8.10 — LAN & collaborative review (SG-8, SG-10)
- **Scope:** Serve the review editor on the local network (`mfo review --host`, optional token auth)
  and make concurrent review safe: per-user edit attribution (the `EditRecord.editor` and history
  from M6/B4 are the basis), optimistic-concurrency / conflict detection on mutations, and basic
  assignment (claim a page/queue range). Stays local-network, private-by-default (NFR-23/24).
- **Satisfies:** SG-8, SG-10; FR-37, FR-42, FR-49; NFR-16, NFR-23.
- **DoD:** Two browsers on the LAN edit one project; edits are attributed per user; a stale write is
  rejected with a clear conflict rather than silently lost.

### Batch 8.11 — Packaging, model tooling & sample data
- **Scope:** Installable distributions (pipx/wheels), a `mfo models` command to download & cache the
  optional models (OCR/detector/translation) with `MFO_MODEL_DIR`, a small bundled **sample dataset**
  and an end-to-end smoke run, and a finalized user guide/README pass.
- **Satisfies:** NFR-12, NFR-22, NFR-28; §15, §21.
- **DoD:** A clean machine can `pipx install mfo`, `mfo models pull <name>`, and run the sample
  project end-to-end (import → export) following only the docs.

---

## Tracking

- [x] M0 Foundation
- [x] M1 Import & Preprocess
- [x] M2 Vision *(MVP scope: 2.1 detect, 2.3 OCR, 2.4 confidence; 2.2 optional ML detector landed)*
- [x] M3 Structure *(MVP scope: 3.1 reading order, 3.2 dialogue grouping; 3.3 optional panel detection landed)*
- [x] M4 Translation *(MVP scope: 4.1 adapter+context, 4.2 glossary/style, 4.3 mapping; 4.4 optional API adapter landed)*
- [x] M5 Render & Export
- [x] M6 Review Editor *(MVP complete — M0–M6 satisfy the DoD §21)*
- [x] M7 AI Refinement *(7.1 assist adapter, 7.2 modes, 7.3 confidence-driven review)*
- [ ] M8 Hardening & Stretch *(planned into batches 8.0–8.11; 8.0 fused detect+recognize, 8.1 parallel processing, 8.2 archive import, 8.3 plugin system, 8.4 panel-aware context, 8.5 cross-volume glossary, 8.6 per-series style presets, 8.7 SFX
detection & transliteration, 8.8 bubble-shape-aware wrapping, 8.9 LLM OCR correction landed)*

When a batch lands: tick it, and append a dated entry to [CHANGELOG.md](CHANGELOG.md) with the
spec IDs it satisfied.
