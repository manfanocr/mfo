# mfo — Manhua Fanyi OCR / Translation

> *manfanocr* — a manga/manhua **OCR + context-aware translation** pipeline and review workstation.

mfo turns a directory of manga/manhua page images into translated, typeset pages —
with **every step inspectable, every region traceable, and the human always in control**.

It is **local-first, modular, and private by default.** AI assistance is optional and never
required for the core workflow.

---

## Status

🧪 **Pre-alpha, but runnable end-to-end.** The full MVP pipeline (import → detect → OCR → translate
→ render → review → export) and the M7 AI-assist layer have landed; M8 hardening/stretch work is in
progress. See [PLAN.md](PLAN.md) for the roadmap and [CHANGELOG.md](CHANGELOG.md) for what has
actually shipped.

## What it does (target workflow)

```
 directory of pages
        │
        ▼
 import → preprocess → detect regions → OCR → reading order
        → group into dialogue units → translate (with context)
        → (optional) AI refine → mask & render text → review/edit → export
```

1. **Point it at a folder** of manga/manhua page images.
2. mfo **detects text regions** (bubbles, narration, SFX, captions) and **OCRs** them
   (Japanese by default, other scripts via adapters).
3. It **reconstructs manga reading order** (right-to-left, top-to-bottom) and **groups**
   regions into dialogue units.
4. It **translates with page/chapter context**, preserving names, honorifics, tone, and
   glossary terms.
5. It **masks the original text** and **renders** the translation back into the bubble.
6. You **review and edit in place**; low-confidence regions are flagged for attention.
7. You **export** translated pages plus a full source→translation mapping.

## Design principles

- **Traceability first.** Every output region links back to its source page, bounding box,
  OCR text, translation history, and human edits. (See invariants I-1…I-8 in the spec.)
- **Inspectable & restartable stages.** Each pipeline stage is cached and can be re-run
  in isolation.
- **Human edits win.** Automation never silently overwrites approved text.
- **Swappable backends.** OCR, detection, translation, and rendering are all adapters.
- **Private by default.** Pages never leave your machine unless you opt in.

## Architecture at a glance

```
core      → data model, project state, pipeline orchestration
vision    → region detection, OCR adapters, layout analysis
language  → translation adapters, glossary, context builder, AI assist
render    → masking, font fitting, text placement, compositing
storage   → project files, SQLite, caches, exports
cli       → headless / scriptable entry point
ui        → local review editor
```

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the data model in
[docs/DATA_MODEL.md](docs/DATA_MODEL.md).

## Quick start

```bash
# install the CLI (isolated global install with pipx; add [all] for the optional engines)
pipx install mfo
pipx install 'mfo[all]'        # manga-ocr + ONNX detector + Argos + review UI
# or, for development:  pip install -e ".[dev]"

# kick the tyres on a generated synthetic sample (fully offline, no downloads)
mfo sample ./sample-pages
mfo init ./sample-project --source ja --target en
mfo import ./sample-project ./sample-pages
mfo run ./sample-project
mfo export ./sample-project --out ./sample-out

# …or on your own pages:
mfo init ./my-volume --source ja --target en
mfo import ./my-volume ./pages         # a folder or a .cbz/.zip
mfo run ./my-volume                     # the full pipeline (or `--stage ocr` for one stage)
mfo review ./my-volume                  # open the local review editor
mfo export ./my-volume --out ./out      # typeset pages + source→translation mapping
```

Optional models (OCR/detector/translation) are managed with `mfo models` and cached under
`MFO_MODEL_DIR` — see [docs/USER_GUIDE.md](docs/USER_GUIDE.md#installing--provisioning-models-mfo-models-mfo-sample).

## Documentation

| Doc | Purpose |
|-----|---------|
| [mfo_design_notes_spec.md](mfo_design_notes_spec.md) | Source specification (goals, FRs, NFRs, invariants) |
| [PLAN.md](PLAN.md) | Milestones, batches, and execution roadmap |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Choosing detectors, OCR engines, and translators (incl. DeepL/API setup) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layer/module design and adapter contracts |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Entities, persistence, project layout |
| [docs/PLUGINS.md](docs/PLUGINS.md) | Registering third-party adapters via entry points |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to build, test, and contribute |
| [CHANGELOG.md](CHANGELOG.md) | Landed batches and notable changes |
| [CLAUDE.md](CLAUDE.md) | Guidance for AI agents working in this repo |

## License

[Apache-2.0](LICENSE).
