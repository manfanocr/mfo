# mfo — Architecture

This expands spec §14 into concrete module boundaries and the adapter contracts that keep the
pipeline modular and swappable (NFR-17/18/19). Pair it with [DATA_MODEL.md](DATA_MODEL.md).

## Principles

Every processing stage is **inspectable, restartable, cacheable, replaceable** (spec §14.2).
Dependencies point **inward toward `core`** — outer layers know about `core`'s models, not the
reverse.

```
                 ┌──────────────────────────────┐
   cli / ui  ──▶ │            core              │ ◀── storage
                 │  models · state · pipeline   │
                 └───────────────┬──────────────┘
                                 │ uses adapters
        ┌───────────────┬────────┴────────┬───────────────┐
     vision          language            render         (ai assist
   detect+OCR      translate+glossary   mask+typeset      lives in
                                                          language)
```

## Layers

### 1. core
Data models (§11), project state, and the **pipeline orchestrator**. The orchestrator runs
stages in order, hashes each stage's inputs, skips unchanged stages, supports
`--stage/--from/--to`, and resumes after interruption (I-5, FR-5, NFR-7/8).

Stage contract:

```python
class Stage(Protocol):
    name: str
    def inputs_hash(self, ctx: ProjectContext) -> str: ...
    def run(self, ctx: ProjectContext) -> StageResult: ...   # reads + writes via storage
```

Stages communicate **only** through persisted state — never in-memory handoffs — so any stage
can be re-run alone.

### 2. vision
- `RegionDetector` — detect text regions / bubbles / SFX, return `Region`s with confidence.
  Default: OpenCV baseline (no model download). Optional: ML detector adapter.
- `OCREngine` — OCR a region → `OCRSpan` (text, confidence, alternatives). Default:
  manga-ocr (JP, vertical). Others via adapters.

### 3. language
- `Translator` — translate batched `TranslationUnit`s with a context bundle. Default: offline
  (Argos/NLLB). Optional: API/LLM adapter.
- `Glossary` / `ContextBuilder` — terminology, names, honorifics, style; assemble nearby/page/
  chapter context (spec §12.5).
- `AIAssistant` (optional) — candidate + literal + readability rewrite + confidence + rationale
  + warnings; assist/review/auto modes (spec §12).

### 4. render
- `Masker` — remove/mask source text, best-effort inpaint, preserve line art (FR-31-33),
  always reversible.
- `TextFitter` — wrap/scale/align text in a region; font, stroke, presets (FR-34-35).
- `Compositor` — paint fitted text onto the masked page → `RenderArtifact`.

### 5. storage
Project directory (§15), `manifest.json`, SQLite for relational/edit data, content-hashed cache
dirs, atomic writes (temp + rename) for crash safety (NFR-10/11). See DATA_MODEL.md.

### 6. cli
Typer app: `init`, `run`, `status`, `review`, `export`. Headless and scriptable (FR-44-47).
Config = file + CLI overrides.

### 7. ui
Local web review app: FastAPI backend over the same `core`/`storage` APIs + a lightweight SPA.
Launched by `mfo review`. Cross-platform via one codebase (NFR-20). A native shell (Tauri/PySide)
is a possible later wrapper, not a separate backend.

## Adapter pattern

Each adapter family has: a `Protocol` interface in `core`, ≥1 **offline default**
implementation, registration via config/entry-points, and lazy/optional heavy deps. This keeps
the core path offline (I-7/I-8) while allowing cloud/GPU opt-ins (NFR-22/24). Plugin hooks
(NFR-19, SG-9) reuse the same registry.

```
config → registry → resolve("ocr", "manga-ocr") → OCREngine instance
```

Resolution is centralized in `core.plugins.resolve_factory`: each layer keeps a built-in
`_FACTORIES` registry, and the `get_*` resolvers consult those built-ins first, then **entry-point
plugins** (groups `mfo.detectors` / `mfo.ocr` / `mfo.translators` / `mfo.assistants` /
`mfo.renderers`). Built-ins win, so the offline defaults can't be shadowed; a broken plugin is
skipped with a warning, never fatally (NFR-9). Third-party adapters register without editing mfo —
see [PLUGINS.md](PLUGINS.md).

## Data flow (one volume)

```
import      → Page[]                          (storage)
preprocess  → Page.preprocessing + cache      (storage)
detect      → Region[]                        (vision)
ocr         → OCRSpan[]                        (vision)
order+group → Region.order, TranslationUnit[] (core heuristics)
translate   → candidates + selected           (language)
[ai refine] → suggestions (optional)          (language)
mask+render → RenderArtifact[]                (render)
review      → EditRecord[] (edits win)        (ui → storage)
export      → images + JSON mapping           (storage)
```

Every arrow persists; every output carries IDs tracing back to source (I-2/I-6).

## Caching & resume

Cache key = stage name + hash(stage inputs + config + model/version refs) (NFR-26/27). A stage
whose key is unchanged is skipped. Caches live under the project's `cache/` so resume is just
"re-run; skip what's current."

## Error handling

Malformed inputs degrade gracefully (NFR-9): skip with a clear, actionable message (NFR-12),
never abort the whole batch. Incremental progress is flushed frequently so a crash loses at most
the in-flight item (NFR-10/11).
