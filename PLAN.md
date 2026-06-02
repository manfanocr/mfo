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

### Batch 2.2 — ML detector adapter (optional)
- **Scope:** Adapter for a trained bubble/text detector (e.g. comic-text-detector / YOLO),
  lazy model download, CPU + optional GPU, region-type classification (bubble/narration/SFX/caption).
- **Satisfies:** FR-11, FR-14 (best-effort), NFR-22; SG-5 groundwork.
- **DoD:** Optional install path; falls back to baseline if model absent; classifies types.

### Batch 2.3 — OCR adapter + Japanese (manga-ocr)
- **Scope:** `OCREngine` adapter interface; manga-ocr adapter (JP, vertical text), per-region
  `OCRSpan` with confidence + alternatives where available; OCR stored separately from
  translation.
- **Satisfies:** FR-6, FR-10, FR-12, FR-13, FR-15; MVP-4; §10.4.
- **DoD:** OCRs detected regions on JP sample; confidence + (when available) alternates stored;
  vertical text handled.

### Batch 2.4 — Confidence surfacing
- **Scope:** Aggregate region/OCR confidence; `mfo status` shows low-confidence counts; flag
  store for downstream highlighting.
- **Satisfies:** I-4, FR-12, NFR-4; MVP-11.
- **DoD:** Low-confidence regions queryable and reported.

---

## M3 — Structure Inference  (MVP-5)

### Batch 3.1 — Reading order
- **Scope:** Manga reading-order heuristic (RTL, top-to-bottom, column-aware), configurable
  direction (FR-17), per-region `reading_order_index`. Manual-override hook for M6.
- **Satisfies:** FR-16, FR-17, FR-20 (data hook); MVP-5; §10.5.
- **DoD:** Correct order on RTL sample pages; LTR/TTB toggle tested.

### Batch 3.2 — Dialogue grouping
- **Scope:** Group regions into `TranslationUnit`s (proximity, type, order); ordered region
  IDs preserved; conversation-chain heuristic.
- **Satisfies:** FR-11, FR-19; G-3; §10.5.
- **DoD:** Units formed and persisted with ordered region refs; tested on sample.

### Batch 3.3 — Panel detection (optional, light)
- **Scope:** Best-effort panel boundary detection to refine reading order; off if not helpful.
- **Satisfies:** FR-18; SG-1 groundwork.
- **DoD:** Panel-aware ordering improves a known tricky sample, or is cleanly disabled.

---

## M4 — Translation  (MVP-6, MVP-7)

### Batch 4.1 — Translation adapter + context builder
- **Scope:** `Translator` adapter interface; context bundle (nearby regions, page, chapter);
  offline adapter (Argos Translate / NLLB via CTranslate2) as default; batch translation of
  linked units.
- **Satisfies:** FR-21, FR-22, NFR-2, NFR-17, NFR-23; MVP-6; §10.6, §12.5.
- **DoD:** Translates units with context offline; results stored as candidates per unit.

### Batch 4.2 — Glossary, terminology & style
- **Scope:** Glossary injection, name/honorific/terminology consistency, style options
  (literal/balanced/natural/localized).
- **Satisfies:** FR-23, FR-24, FR-25; SG-2/3/4 groundwork; §12.5.
- **DoD:** Glossary terms enforced; style toggle changes output; consistency test.

### Batch 4.3 — Traceability & mapping export
- **Scope:** Selected translation per unit, full link graph source→OCR→translation, JSON export
  of mappings; `EditRecord` scaffolding.
- **Satisfies:** I-2, I-6, FR-26 (data), FR-41/42/43; MVP-7; §21.
- **DoD:** `mfo export --mapping` emits JSON tracing every output region to its source.

### Batch 4.4 — API translation adapter (optional)
- **Scope:** Opt-in cloud/LLM translation adapter behind explicit config; never default.
- **Satisfies:** NFR-24/25, §14.3; FR-21.
- **DoD:** Works when configured; core remains fully offline without it.

---

## M5 — Rendering & Export  (MVP-9)

### Batch 5.1 — Text masking / removal
- **Scope:** Mask original text within regions; best-effort background reconstruction (inpaint);
  preserve line art; always reversible (keep originals).
- **Satisfies:** FR-31, FR-32, FR-33, I-1, I-6; §10.8.
- **DoD:** Masked layer produced; original recoverable; line-art preservation sanity test.

### Batch 5.2 — Font fitting & placement
- **Scope:** Text wrap/scale/align within bbox, font selection, stroke/outline, style presets,
  bubble-aware fitting.
- **Satisfies:** FR-34, FR-35, NFR-3; SG-6 groundwork; §10.8.
- **DoD:** Text fits sample bubbles without overflow; presets applied; deterministic output.

### Batch 5.3 — Composite & export pages
- **Scope:** Render translated text onto (masked) page, export images + project records +
  optional transcript/manifest.
- **Satisfies:** FR-14, FR-43, MVP-9, NFR-26; §7.6, §10.8.
- **DoD:** `mfo export` produces translated pages + JSON mapping for the sample volume.

---

## M6 — Review Editor  (MVP-8)  → completes MVP

### Batch 6.1 — Review backend/API
- **Scope:** FastAPI service exposing pages, regions, OCR, translations, confidence, edit
  history; mutation endpoints that write `EditRecord`s; precedence of edits over automation.
- **Satisfies:** I-3, FR-37, FR-42, FR-49; §13.2.
- **DoD:** API serves and mutates project state; edits persisted as records.

### Batch 6.2 — Local web editor UI
- **Scope:** `mfo review` launches local app: image canvas, clickable regions, side panel
  (OCR/translation/history/confidence), keyboard navigation, zoom/pan, dark mode.
- **Satisfies:** FR-36, NFR-13/14/15/16; §13.1–13.5.
- **DoD:** Open a project, click regions, see all data, navigate by keyboard.

### Batch 6.3 — In-place editing & region ops
- **Scope:** Edit translation in place, adjust font/breaks/align/position, split/merge regions,
  status flags (correct/needs-review/ignore/manual), re-render preview, low-confidence-first
  review queue, manual reading-order correction.
- **Satisfies:** FR-20, FR-37, FR-38, FR-39, FR-40, FR-26; §13.3/13.4.
- **DoD:** All region ops persist and re-render; review queue surfaces low-confidence first.

---

## M7 — AI-Assisted Refinement (post-MVP)

### Batch 7.1 — AI assist adapter
- **Scope:** Optional LLM adapter producing candidate + literal + readability rewrite +
  confidence + rationale + warnings; bubble-fit shortening; speaker-shift hints.
- **Satisfies:** FR-27, FR-28, FR-30; §12.1/12.3/12.5.
- **DoD:** Produces structured suggestions; disabled by default; offline core unaffected.

### Batch 7.2 — AI modes
- **Scope:** Assist / Review / Auto modes (§12.4); auto applies only high-confidence and keeps
  full audit trail; never overwrites approved text.
- **Satisfies:** I-3, I-7, FR-29; §12.4.
- **DoD:** Each mode behaves per spec; auto-applied changes are auditable and reversible.

### Batch 7.3 — Confidence-driven review integration
- **Scope:** Wire AI confidence + uncertainty into the M6 review queue and flags.
- **Satisfies:** I-4, FR-30, NFR-4.
- **DoD:** AI-flagged regions appear in the review queue with rationale.

---

## M8 — Hardening & Stretch

- **Perf & parallelism** (NFR-5/6/7/8): parallel page processing, profiling, cache tuning.
- **Plugin hooks** (NFR-19, SG-9): formal extension points for detectors/OCR/translators/heuristics.
- **Archive formats** (NG-4 relaxation): CBZ/ZIP import.
- **Stretch goals** (SG-1…SG-10): panel-aware context, character-name memory across volumes,
  shared terminology DB, per-series style presets, SFX transliteration, OCR correction via LLM,
  collaborative review, web review over LAN.
- **Packaging & docs**: installable distributions, model-download tooling, user guide, sample
  data set.

---

## Tracking

- [x] M0 Foundation
- [x] M1 Import & Preprocess
- [ ] M2 Vision
- [ ] M3 Structure
- [ ] M4 Translation
- [ ] M5 Render & Export
- [ ] M6 Review Editor *(MVP complete)*
- [ ] M7 AI Refinement
- [ ] M8 Hardening & Stretch

When a batch lands: tick it, and append a dated entry to [CHANGELOG.md](CHANGELOG.md) with the
spec IDs it satisfied.
