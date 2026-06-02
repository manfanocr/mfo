# CLAUDE.md — guidance for AI agents working in mfo

This file orients any AI agent (Claude Code or otherwise) contributing to mfo. Read it before
making changes.

## What this project is

mfo is a **local-first manga/manhua OCR + context-aware translation pipeline** with an
interactive review editor. The authoritative requirements live in
[`mfo_design_notes_spec.md`](mfo_design_notes_spec.md); the execution roadmap lives in
[`PLAN.md`](PLAN.md). When in doubt, those two files win over assumptions.

## Source of truth & where to look

| You need… | Read… |
|-----------|-------|
| What/why (requirements, invariants) | `mfo_design_notes_spec.md` |
| What to build next, in what order | `PLAN.md` (milestones → batches) |
| How the code is organized | `docs/ARCHITECTURE.md` |
| Entities & persistence | `docs/DATA_MODEL.md` |
| What already shipped | `CHANGELOG.md` |
| How to build/test/style | `CONTRIBUTING.md` |

## Workflow rules

1. **Work batch-by-batch.** Pick the next unchecked batch in `PLAN.md`. Keep each PR to one
   batch; leave `main` green.
2. **When a batch lands:** tick it in `PLAN.md` *and* move its entry into `CHANGELOG.md` under a
   dated heading, citing the spec IDs it satisfied.
3. **Cite spec IDs** (`FR-*`, `NFR-*`, `I-*`, `MVP-*`) in commits/PRs so traceability is real.
4. **Tests with every batch** (NFR-29). Critical stages must have coverage. Don't claim a
   batch is done without running the tests.
5. **Don't silently change scope.** If the spec is ambiguous, prefer the decision already
   recorded in `PLAN.md`'s "Tech decisions" table; if none exists, surface the choice rather
   than guessing.

## Invariants you must never break

These come straight from the spec (§5) and override convenience:

- **I-1 / FR-3** — Never destroy or mutate source images. All derived data goes in the project
  directory; originals are read-only.
- **I-2 / FR-41-42** — Maintain stable IDs and the full source → OCR → translation → render
  link graph. Traceability is *the* core feature.
- **I-3 / FR-29** — User edits take precedence; automation never silently overwrites approved
  text.
- **I-4** — Keep confidence/uncertainty visible, not hidden.
- **I-5** — Every stage stays inspectable, restartable, and cacheable.
- **I-7 / I-8** — AI assistance is optional; the core pipeline runs fully offline. Never make a
  network call mandatory for the core path.

## Architectural conventions

- **Layers:** `core` (model, state, orchestration) · `vision` (detect, OCR) · `language`
  (translate, glossary, AI) · `render` · `storage` · `cli` · `ui`. Don't create cross-layer
  shortcuts; depend inward toward `core`.
- **Adapters over providers** (NFR-17): OCR, detection, translation, rendering, and AI are all
  pluggable interfaces with at least one **offline default**. Add new providers as adapters;
  never hard-code a vendor into a stage.
- **Stages are pure-ish:** read inputs from storage, write outputs back, cache by input hash.
  No hidden global state.
- **Serializable everything:** intermediate data must round-trip to disk (JSON/SQLite).

## Tech stack (defaults — see PLAN.md §"Tech decisions")

Python ≥3.11 · Pillow + OpenCV + NumPy · SQLite + JSON manifest · Typer (CLI) · FastAPI
(review UI) · pytest + ruff + mypy. OCR default: manga-ocr (JP). Translation default: offline
(Argos/NLLB). Cloud/LLM adapters are opt-in only.

## Code style

- Format & lint with **ruff**; type-check with **mypy**. Match surrounding code.
- Prefer explicit data structures over hidden state (spec §20).
- Keep functions small and stage boundaries clean. Comment density should match neighbors.

## Definition of done (project-level, spec §21)

A user can: point mfo at a folder of pages → detect & OCR → get context-aware translations →
review/edit in place → export → reopen later with all mappings intact. That equals
**M0–M6 complete** in `PLAN.md`.
