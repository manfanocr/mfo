# Changelog

All notable changes to mfo are recorded here. Landed **batches** (from [PLAN.md](PLAN.md)) are
moved here when complete, with the spec IDs they satisfied.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches `0.1.0`.

## [Unreleased]

### M8 — Hardening & Stretch

#### Added — Batch 8.2: Archive import (CBZ/ZIP) (2026-06-03)
- **`mfo import` reads `.cbz`/`.zip` archives, not just folders.** A CBZ is just a ZIP of images, so
  `mfo.vision.ingest` gained `is_archive`/`ARCHIVE_SUFFIXES`/`extract_archive` and `discover_images`
  now takes an `extract_to` staging dir: for an archive it extracts the page images into the project
  cache **read-only** (the source archive is never opened for writing — I-1), then discovers them
  exactly like a directory, applying the same natural-sort/`--order`/`--manifest` strategies. Entries
  are flattened to their basenames and only the basename is ever used as the destination, so a
  malicious archive can't escape the staging dir (no zip-slip). Non-image entries (`ComicInfo.xml`,
  hidden `.`/AppleDouble files, `__MACOSX/` resource forks) are ignored; a corrupt entry or a
  duplicate basename is skipped with a clear warning rather than aborting the import (NFR-9); a wholly
  unreadable archive raises a clear error.
- **Replayable under `mfo run`.** `ImportStage` stages the archive into the same cache dir and folds
  the archive's size + mtime into its `inputs_hash`, so a reopened project resumes the import and a
  changed archive re-triggers it. CBR/RAR is out of scope (needs a non-free dependency) and noted in
  `docs/USER_GUIDE.md`. (FR-1, FR-2, I-1, NFR-9; relaxes NG-4)

#### Added — Batch 8.1: Parallel processing & performance tuning (2026-06-03)
- **`--jobs`: process pages concurrently.** The heavy per-page stages (preprocess, detect, OCR,
  translate, render, composite) now run several pages at once. Each stage was restructured into a
  *plan → compute → persist* shape: it reads its inputs and decides which pages need work serially,
  runs only the pure injected callable (detect/recognize/translate/mask/composite) across pages on a
  bounded thread pool (`mfo.core.parallel.parallel_map`, order-preserving), then writes every DB row
  and file back serially in page order. So all SQLite access stays single-threaded and the result is
  **byte-identical regardless of the worker count** — `--jobs` only affects speed, never output, and
  it is deliberately kept out of every stage's cache key so unchanged pages still skip (NFR-8).
  Threads (not processes) are used because the callables spend their time in native code or network
  I/O that releases the GIL, and they share the one DB connection without pickling. Added `--jobs/-j`
  (default `1`, `0` = auto/CPU count capped at 8) to `detect`, `ocr`, `translate`, `render`,
  `export`, and `run`.
- **`mfo bench` harness.** Force-re-runs each configured heavy stage and reports per-stage and total
  wall-clock at a given `--jobs`, so the speedup is measurable on real pages; it times against an
  in-memory run state so it doesn't disturb the project's pipeline progress. Documented in
  `docs/USER_GUIDE.md` (parallelism + benchmarking). (NFR-5, NFR-6, NFR-7, NFR-8; §20)

#### Added — Auto-merge overlapping detected regions (2026-06-03)
- **One region per bubble.** Detectors (especially PaddleOCR) often split a single speech bubble
  into several overlapping line-boxes, which then OCR and render as fragments. `get_detector` now
  wraps every detector so overlapping output boxes are merged into one region (`MergingDetector` +
  the pure `merge_overlapping_regions`): boxes that overlap by ≥ a fraction of the smaller box's
  area are joined transitively (a column of stacked lines collapses to one), taking the union box,
  the most conservative member confidence (I-4), and the largest member's type. A merged region's
  per-line recognized `text` (from a det+rec detector) is dropped so the OCR stage re-reads the
  whole merged crop in correct order; IGNORE panels/frames are never merged. On by default; tune
  with `mfo detect --overlap-frac` or disable with `--no-merge-overlap` (both persisted so `mfo run`
  reproduces them). Documented in `docs/USER_GUIDE.md`. (FR-11, FR-39 spirit; NFR-8, NFR-17; I-4)

#### Added — Batch 8.0: Fused detect+recognize for det+rec engines (2026-06-03)
- **`paddle-rec` detector — PaddleOCR detect+recognize in one pass, reused by OCR.** PaddleOCR
  recognizes while it detects, so using `--detector paddle` then `--engine paddleocr` ran paddle's
  recognition twice. The new `paddle-rec` detector (`PaddleRecDetector`) runs the full pipeline once
  and carries the recognized text + **real per-box confidence** on each `DetectedRegion` (new
  optional `text`/`text_confidence`; replaces the `0.9` placeholder for these boxes — I-4). The
  detect stage persists that as a provisional, provenance-tagged span (new `OCRSpan.source`, I-2)
  and records `detection.recognized`; `ocr_regions(reuse_detection=True)` then **adopts** those
  spans, recognizing only regions that lack detection text — so `mfo ocr` does no second paddle pass.
  Detect and OCR stay separate, restartable, separately-cached stages (I-5): `mfo ocr
  --no-reuse-detection` (or `--force`, or a different `--engine`) recognizes everything with the
  chosen engine, keeping `--engine` authoritative. `get_detector` now takes `lang=` (forwarded to
  recognizing detectors; detection-only ones ignore it) and `mfo run` threads the project source
  language into both detect and OCR. Re-detection clears prior provisional spans (no orphans).
  Tested entirely with fakes — no paddle install required. Documented in `docs/USER_GUIDE.md`.
  (FR-12, FR-13; NFR-7, NFR-8, NFR-17; I-2, I-4, I-5)

### M7 — AI-Assisted Refinement

#### Added — Batch 7.3: Confidence-driven review integration (2026-06-03)
- **AI uncertainty wired into the review queue and flags** (`mfo.core.confidence.ai_candidate`;
  `mfo.ui.review`): the optional AI layer's per-unit confidence and rationale now drive review just
  like OCR/detection confidence does (I-4). `review_queue` flags a region when its unit's `ai`
  candidate is low-confidence (unknown or below the queue threshold) and sorts AI-flagged regions to
  the top **beside** low-confidence ones — so a unit with confident OCR but an uncertain translation
  still surfaces — each entry carrying `ai_flagged`/`ai_confidence`/`ai_rationale`. `project_summary`
  gains a per-page `ai_flagged` count and `page_view` region payloads carry the same AI fields.
  The bundled editor surfaces it: an **AI** badge + rationale tooltip on flagged queue rows, a violet
  ring on flagged regions, the AI rationale (reasoning, ambiguity warnings, speaker-shift hints) on
  the candidate card, and an AI-flagged total in the status line. Entirely off the core path — a unit
  with no AI candidate is never flagged, so projects that never run `mfo assist` are unaffected (I-7).
  (FR-30, NFR-4; I-4)

#### Added — Batch 7.2: AI modes (2026-06-03)
- **AI application stage + three modes** (`mfo.storage.assist`, `AssistMode`, `mfo assist`): runs the
  7.1 assistant over a project's translated units and persists structured suggestions as candidates,
  resolving the *selection* per `--mode` (§12.4) — **assist** (suggest only), **review** (highlight
  the AI candidate), **auto** (apply only when confidence ≥ `--min-confidence`, default `0.8`). The
  AI primary becomes an `ai` candidate (carrying confidence + a rationale that folds in the shortened
  alternative, warnings, and speaker-shift hint); distinct literal/readability renderings become
  `literal`/`natural` candidates for side-by-side comparison. Across every mode it only **appends**
  candidates, **never** changes the selection of a human-approved (`manual`) unit (FR-29), and
  records each selection change it does make as an `EditRecord` (`editor="ai:<mode>"`) so it stays
  auditable and reversible (I-3). The suggestion callable is injected (storage stays provider-free),
  the refined draft is the machine/human translation (never a prior AI output) so re-runs are
  idempotent, and a page-level signature skips unchanged pages (NFR-8). New `Page.assist` provenance;
  the `mfo assist` command (`--mode/--assistant/--min-confidence/--style/--force`) is **opt-in and
  not part of `mfo run`**, configured from `MFO_AI_*` (falling back to `MFO_API_*`), so the offline
  core is untouched (I-7). Documented in `docs/USER_GUIDE.md`. (FR-29; §12.4; I-3, I-7)

#### Added — Batch 7.1: AI assist adapter (2026-06-03)
- **AI assist layer** (`mfo.language.assist`): an opt-in LLM adapter (`AiAssistant` protocol +
  `LlmAssistant` over any OpenAI-compatible endpoint) that turns a recognized line and its draft
  translation into a **structured** `AssistSuggestion` — candidate, literal rendering, readability
  rewrite, bubble-fit shortened alternative, confidence (clamped to `[0, 1]`), rationale, free-form
  warnings, and a likely-speaker-shift hint. It is **never on the core path**: no offline default
  assistant, lazy, configured entirely from `MFO_AI_*` env vars (falling back to the `MFO_API_*` set
  so one endpoint serves translation and AI review), sending only text and context — never the page
  image — through the shared injectable transport, so it is unit-testable offline and adds no hard
  dependency. Reply parsing is defensive (code-fence stripping, missing/partial fields degrade to
  `None`/`[]`, confidence clamped) and uncertainty is surfaced, not hidden. Wiring this into AI
  *modes* and the review queue is batches 7.2/7.3. (FR-27, FR-28, FR-30; §12.1/12.3/12.5; NFR-17,
  NFR-24, NFR-25)

### Post-MVP — review-editor & engine improvements (outside the PLAN batches)

Hardening and feature work on top of the completed M0–M6 MVP, driven by real use of the review
editor. Not numbered PLAN batches; grouped here by kind. Each shipped with tests (NFR-29).

#### Added
- **Undo/redo (server-side, persistent).** Every review mutation — region ops *and* translation
  edits — now records a page snapshot (`mfo.storage.history`), so changes are undoable and survive
  reopen. Undo restores the before-snapshot, redo the after; a new edit truncates the per-page redo
  tail. The same stack serves a **global** history and a **per-page** one (page states are
  independent), exposed with a scope toggle in the editor and `Ctrl+Z` / `Ctrl+Y` (and
  `Ctrl+Shift+Z`). New `HistoryEntry` entity + DB **migration 003**; routes `POST /api/undo`,
  `POST /api/redo`, `GET /api/history`. The append-only `EditRecord` audit log is intentionally not
  snapshotted, so undo reverts state while the audit stays truthful. (FR-42; I-2, I-3)
- **PaddleOCR adapters.** A `paddleocr` OCR engine (JP/ZH/EN/KO, picks its model from the project's
  source language) and a `paddle` text-box region detector (baseline fallback, like `ml`), behind
  the new optional `ocr-paddle` extra and lazily imported so the offline core is untouched.
  (FR-6, FR-10, FR-11; NFR-17, NFR-21)
- **DeepL translator adapter** (`deepl`): opt-in, never default, configured entirely from
  `MFO_DEEPL_*` env vars (nothing secret persisted), sending only the unit's text through the shared
  injectable transport — unit-testable offline with no hard dependency. (FR-21; NFR-17, NFR-24, NFR-25)
- **`docs/USER_GUIDE.md`** — how to choose and configure every detector, OCR engine, and translator
  (argos / api / deepl, paddle, the ML detector), including DeepL free vs. pro, pointing the `api`
  adapter at OpenAI-compatible gateways, and why a "free Google Translate" path isn't bundled
  (unofficial / ToS). Linked from the README.
- **Create / delete region in the editor.** Draw a box on the canvas → it is OCR'd and translated
  immediately as a `MANUAL` unit (automation won't clobber it, I-3); delete a region the detector
  got wrong (drops its OCR, detaches it from units, removes a now-empty unit). Region boxes resize
  from any edge or corner (eight handles) and drag to move. (FR-38; §13.3)
- **Per-region jobs.** Re-run OCR on the selected region and re-translate its unit on demand
  (undoable; a fresh machine candidate preserves any human/AI candidate and selection — I-3). Routes
  map a missing optional engine to a clear `503`. (FR-12, FR-15, FR-21; I-3)
- **Review-editor UX:** remembers the last viewed page per project and reopens on it; page-list
  counts refresh live after edits; a "Needs review" queue filter with `↑`/`↓` stepping through it;
  an opt-in click-to-recenter toggle (arrow/queue navigation always recenters); and a top-bar Render
  button that renders the current page and shows the result. (FR-36; §13)

#### Changed
- **One translation unit per bubble by default.** Grouping no longer chains nearby bubbles into a
  shared unit (chaining is now opt-in via `mfo group --max-gap >0`), so each bubble's translation is
  typeset into its own box instead of overflowing the union box of a merged chain. To keep
  cross-bubble context, the default neighbour window widened 1→2 (more surrounding dialogue in each
  unit's context bundle, consumed by the context-aware `api` adapter). (FR-19, FR-22; I-2; NFR-8)
- **Oversized/frame detections are auto-ignored.** The connected-components baseline can't tell a
  bubble from a panel, so blobs that are oversized or span most of the page width are now auto-marked
  `ignore` — kept in the data (I-1/I-2) but skipped by OCR, rendering, and the review queue — instead
  of being trusted as bubbles. The `paddle`/`ml` detectors box actual text and avoid this. (I-4)
- The review SPA's assets are served with `Cache-Control: no-cache`, so an updated `app.js`/`app.css`
  is never masked by a stale browser copy.

#### Fixed
- The PaddleOCR engine and detector now target PaddleOCR **3.x** (`predict()` API; the old
  `show_log` / `.ocr(det=, rec=, cls=)` calls were removed upstream and raised
  `ValueError: Unknown argument: show_log`). A missing/broken `paddlepaddle` backend is caught at
  model construction and surfaced as a clear `OcrDependencyError` (OCR) or transparently falls back
  to the baseline detector (`mfo detect --detector paddle`), instead of an ugly traceback. (I-7, NFR-17)
- A missing Argos language package now raises a clear, actionable `TranslatorDependencyError` naming
  the package to install, instead of Argos's cryptic `'NoneType' … get_translation` AttributeError. (I-7)
- The review page list keyed off the wrong field (`id` vs. the API's `page_id`), so pages weren't
  selectable — fixed across the SPA.
- Invisible click-blocking overlays (the HTML `hidden` attribute overridden by an explicit CSS
  `display`) left the "Select a page" placeholder, the inspector panel, and region clicks dead;
  fixed with `[hidden] { display: none !important }`, restoring region selection, the side panel, and
  shift-click-to-merge.

### Added
- **Batch 4.4 — API translation adapter** (M4 Translation — optional, opt-in, post-MVP):
  - `mfo.language.translate.ApiTranslator`: an opt-in cloud/LLM translator behind the existing `Translator`
    interface, talking to any OpenAI-compatible chat-completions endpoint. It is **never the default**
    (NFR-24) and is configured entirely from environment variables (`MFO_API_KEY`, `MFO_API_BASE_URL`,
    `MFO_API_MODEL`, `MFO_API_TIMEOUT`) so no endpoint or secret is ever written to the project (NFR-25);
    only the translator *name* is persisted. It sends the unit's text and context bundle — nearby dialogue,
    page locator, and pinned glossary terms (FR-22, FR-24) — under the requested register (FR-25), and never
    the source page image (NFR-25). An empty unit short-circuits with no network call, and a missing key
    raises `TranslatorDependencyError` only at translate time (mirroring the offline adapter's lazy check),
    so config can be saved before a key is set. Its `version` folds in the model so switching models re-runs
    the translation cache (NFR-8).
  - Network I/O lives behind an injectable `ApiTransport` (default: a stdlib-`urllib` JSON POST), so the
    adapter adds **no hard dependency** and is exercised entirely offline in tests; connection failures and
    non-JSON/malformed responses surface as a clear `TranslatorDependencyError` (caught by the CLI). Registered
    as the `"api"` translator, selectable via `mfo translate --translator api` (and reproduced by `mfo run`).
    The offline Argos default path is byte-for-byte unchanged and makes no network call (I-7/I-8, DoD 4.4).
  - Tests: registry resolves `argos`/`api` and lists alternatives on an unknown name; offline adapter empty
    no-op; API adapter makes no call for an empty source or before a key is set; request shaping (URL join,
    auth header, model, style guidance, glossary, preceding/following context, the line itself) and response
    parsing/trimming; malformed-response error; model-folded cache id; env-driven construction with nothing
    secret persisted; and the default transport's POST/JSON glue + network-error wrapping via a fake `urlopen`.
    CLI: `mfo translate --translator api` persists the name only (no key/endpoint on disk — NFR-25).
  - Satisfies: FR-21; NFR-17, NFR-24, NFR-25; §14.3.
- **Batch 3.3 — Panel detection** (M3 Structure — optional, light, post-MVP):
  - `mfo.vision.panels`: a best-effort, dependency-light panel detector (recursive X–Y cut over the page's
    white gutters, using only OpenCV/NumPy — no model download, NFR-21). `detect_panels` recovers frame
    rectangles for the common grid layouts as source-pixel `BBox`es; a borderless/art-bleed page simply
    yields one whole-page panel so the caller degrades to the flat heuristic (best-effort — FR-18).
    `detect_panels_file` loads a page read-only (I-1).
  - `mfo.core.reading_order.order_regions_by_panels`: pure, offline panel-aware ordering — panels are read
    in reading order, each panel's regions in reading order, then concatenated; regions outside every frame
    fall to the end. This fixes the layout the flat tier scan misorders (a tall panel spanning tiers beside
    a stack of shorter ones), where flat yields `rt, l, rb` and panel-aware the human `rt, rb, l` (DoD 3.3).
    The shared tier logic was refactored into `_order_indices`, reused by both region and panel ordering.
  - `assign_reading_order` gained an injected, optional `detect_panels` callable (storage stays imaging-free,
    mirroring the detect stage); the panel mode is folded into the per-page signature so toggling it re-runs
    (NFR-8) while the default path stays the fully-offline geometry heuristic. Provenance records `panels` and
    `panel_count`. Exposed via `mfo order --panels` and the `StructureStage` (`reading-order@2`), persisted in
    project config so `mfo run` reproduces it (FR-48).
  - Tests: detector coverage (blank → none; single frame → one; 2×2 grid → four; tall-panel-beside-stack → three;
    speck filtered; config override; read-only file load); pure-ordering coverage (flat misorders vs. panel-aware
    corrects; multi-region-per-panel; outside-panel regions last; empty-panel fallback; empty input); and storage
    coverage (panel-aware reorders + records provenance; flat records none; toggling re-runs; panel mode idempotent).
  - Satisfies: FR-18; SG-1 groundwork.
- **Batch 2.2 — ML detector adapter** (M2 Vision — optional, post-MVP):
  - `mfo.vision.detect`: a trained bubble/text detector behind the existing `RegionDetector` interface,
    fully optional so the offline core is untouched (I-7/I-8, NFR-21/22). `OnnxDetectionModel` imports
    `onnxruntime` lazily and fetches the model weights on first use — resolving them from `model_dir`
    (env-overridable `MFO_MODEL_DIR`) or downloading atomically from a configured `model_url` — and runs
    on CPU by default with GPU opt-in via `providers`. The model is decoupled behind a `DetectionModel`
    protocol so the adapter's logic carries real test coverage without the heavyweight runtime: letterbox
    pre-processing, `decode_detections` (un-pad/un-scale model boxes to source pixels), `classify_region`
    (model class index → BUBBLE/NARRATION/SFX/CAPTION, UNKNOWN out of range — FR-11, FR-14), confidence
    thresholding, greedy IoU `non_max_suppression`, page clamping, and reading-order sort.
  - `FallbackDetector` wraps the ML detector with the connected-components baseline: it resolves its
    backend once, lazily, on first detect, and on a missing dependency/model (`DetectorDependencyError`)
    transparently falls back to the baseline rather than hard-failing (DoD 2.2). Its `name`/`version` is a
    stable composite (`ml-detector+fallback@1+1`) so the detection cache signature stays deterministic
    regardless of which backend runs (NFR-8). Registered as the `"ml"` detector, selectable via
    `mfo detect --detector ml` (and `mfo run`), and shipped as the `detect` extra (`pip install 'mfo[detect]'`).
  - Tests: class→type mapping incl. out-of-range; NMS overlap suppression; letterbox-aware decode geometry;
    adapter threshold/NMS/clamp/order with a fake model; fallback uses the primary when available and drops
    to the baseline when the model is missing; `get_detector("ml")` resolution; model-dir env override;
    `ensure_model_file` errors without a URL and downloads atomically (temp cleaned) with one; and a clear
    missing-dependency error when `onnxruntime` is absent.
  - Satisfies: FR-11, FR-14 (best-effort); NFR-17, NFR-21, NFR-22; SG-5 groundwork.
- **Batch 6.3 — In-place editing & region ops** (M6 Review Editor — completes M6 / the MVP):
  - `mfo.ui.review`: the framework-free service grew the editing operations of §13.3/13.4 as pure functions
    over a `ProjectStore`, each persisting directly and returning the refreshed page view: `set_region_status`
    flags a region correct / needs-review / ignore / manual (FR-40) — a user choice automation won't clobber
    (I-3); `move_region` repositions/resizes a region's box (FR-38); `reorder_regions` applies a manual
    reading-order correction (FR-20); `split_region` cuts a region in two (horizontal or vertical, at a ratio),
    inserting the new piece right after the original in reading order and in any unit that contained it; and
    `merge_regions` unions two+ regions on a page into the earliest, moving the others' OCR onto it so no
    transcription is lost (I-2). `review_queue` orders every region with low-confidence ones first (§13.4,
    I-4). `rerender_page` re-runs the offline mask + composite stages (sharing the CLI's `composite@1`
    signature, so only changed pages recompute — NFR-8) and `page_render_path` serves the preview (I-1).
  - `mfo.ui.server`: new routes over those ops — `PUT /api/regions/{id}/status`, `PUT /api/regions/{id}/bbox`,
    `POST /api/regions/{id}/split`, `POST /api/regions/merge`, `PUT /api/pages/{id}/order`,
    `GET /api/review-queue`, and `POST`/`GET /api/pages/{id}/render`. A `ValueError` from a rejected op (bad
    status, ratio, or non-permutation order) now maps to HTTP 400, alongside the existing 404 for missing
    entities.
  - `mfo/ui/static/`: the SPA became a real editor. The side panel edits the translation in place (textarea +
    Save, Ctrl+Enter), reverts to any candidate by clicking it, and flags status (buttons + keys 1-4). On the
    canvas, the selected region drags to move and has a corner handle to resize (persisted on mouse-up);
    shift-click marks regions for a merge; Split / Merge / reading-order nudge buttons and `s`/`m` keys drive
    the structural ops. A Queue panel lists regions low-confidence-first with `n`/`p` to step through them, and
    a Preview toggle (`r`) overlays the re-rendered page, dropping itself when a later edit invalidates it.
  - Tests: service-layer coverage for every op (status set/persist + reject unknown; move + reject negative
    size; reorder + reject non-permutation; split geometry/unit insertion + reject bad ratio; merge union +
    OCR move + unit collapse + needs-two; queue low-confidence-first; re-render produces a PNG and the
    pre-render path 404s) and HTTP coverage (status, bbox, split↔merge round-trip, order, queue, render
    endpoints incl. 404-before-render and 400-on-bad-input).
  - Satisfies: FR-20, FR-26, FR-37, FR-38, FR-39, FR-40; I-2, I-3, I-4; spec §13.3, §13.4.
- **Batch 6.2 — Local web editor UI** (M6 Review Editor):
  - `mfo.ui.server`: `create_app` now also serves the bundled single-page editor — `GET /` returns the
    editor HTML and `/static/*` serves its CSS/JS, mounted after the API routes so it never shadows them.
    A new `serve(store, host, port)` runs the app locally with uvicorn (binding to `127.0.0.1` by default
    so the editor stays a local-first, offline tool — I-7/I-8); uvicorn ships with the `review` extra and is
    imported lazily, so `create_app` and the tests need only FastAPI.
  - `mfo/ui/static/`: a dependency-free SPA (`index.html` + `app.css` + `app.js`, no build step) over the
    batch-6.1 read API. It lists the project's pages with low-confidence badges (§13.1), draws the page image
    on a zoom/pan canvas with clickable region overlays — low-confidence regions visually distinct (I-4,
    §13.5) — and shows the selected region's metadata, OCR, translation, candidates (selection marked) and
    edit history in a side panel (§13.2). Keyboard navigation steps through regions in reading order and
    across pages, with zoom/pan/fit and a persisted dark mode (FR-36, NFR-13/14/15/16, §13.5). In-place
    editing and region ops follow in batch 6.3; this batch is read + navigate.
  - CLI: `mfo review <project>` now launches the local web editor (was a placeholder), opening the store with
    `check_same_thread=False` for the threaded server and exiting cleanly with an actionable message when the
    `review` extra is absent; `--host`/`--port` configurable. (`_not_yet` removed — every command is now live.)
  - Tests: the FastAPI client now also asserts `GET /` serves the editor page and `/static/app.{js,css}` serve
    the SPA assets (skipped when the `review` extra is absent).
  - Satisfies: FR-36; NFR-13, NFR-14, NFR-15, NFR-16; spec §13.1-13.5.
- **Batch 6.1 — Review backend/API** (M6 Review Editor):
  - `mfo.ui.review`: the framework-free heart of the review backend — pure functions over a `ProjectStore`
    that assemble the page-editor payloads (`project_summary` for the main screen's per-page index;
    `page_view` exposing each page's regions with their OCR, aggregate confidence, and status, plus its
    translation units with full candidate lists and edit history; `unit_view`; `page_image_path` for the
    canvas, read-only — I-1) and apply the two edits review needs now: `edit_translation` (FR-37) lands
    user text as a `MANUAL` candidate and selects it, and `select_candidate` (FR-49) revisits a prior
    decision. Every mutation appends an immutable `EditRecord` (FR-42) and lands the choice as the
    *selected* translation, so the translate stage preserves it and automation never silently overwrites
    approved text (I-3). Confidence stays visible per region (I-4). Keeping this layer HTTP-free makes the
    logic fully testable without a server.
  - `mfo.ui.server`: a thin FastAPI shell over the service — `create_app(store)` wires read routes
    (`GET /api/project`, `/api/pages/{id}`, `/api/pages/{id}/image`, `/api/units/{id}`) and mutation routes
    (`PUT /api/units/{id}/translation`, `POST /api/units/{id}/select`), mapping a not-found entity to HTTP
    404. FastAPI is an optional dependency (the new `review` extra: `fastapi` + `uvicorn`); importing the
    server without it raises a clear, actionable error, so the offline core stays dependency-light
    (I-7/I-8). The launcher (`mfo review`) arrives in batch 6.2 and builds on `create_app`.
  - `mfo.storage`: `Database.open` / `ProjectStore.open` / `ProjectStore.create` gained an opt-in
    `check_same_thread=False` so the threaded review server can use one SQLite connection across worker
    threads; the default is unchanged, so every existing caller is unaffected.
  - Tests: service layer (read views expose regions/OCR/units/confidence; `edit_translation` records a
    manual candidate + `EditRecord`; repeated edits reuse one manual candidate but record each; `select_candidate`
    reverts to a machine candidate and records it; unknown page/unit/candidate raise; a manual edit survives a
    forced re-translation — I-3); HTTP layer via FastAPI's in-process client (serves project/page/image, edits
    and persists records, selects, 404s), skipped when the `review` extra is absent.
  - Satisfies: FR-37, FR-42, FR-49; I-1, I-3, I-4; spec §13.2.
- **Batch 5.3 — Composite & export pages** (M5 Rendering & Export):
  - `mfo.render.composite`: a pure, storage-free compositor — the last render step. `composite_page` takes a
    page (normally the masked layer) and a list of `Placement`s (translated string + box + style) and, for
    each, typesets it with `fit_text` and pastes the tile using its own alpha as the mask, so glyphs blend
    on and the transparent surround leaves the art untouched; the original image is never mutated (I-1) and
    the same page + placements yield byte-identical output (NFR-26). It returns the finished page plus the
    per-placement layouts, so the count of placements that overflowed their box stays visible (I-4).
    `composite_file` reads a base page (read-only) and returns the composited PNG bytes for persistence.
  - `mfo.storage.render`: `composite_pages` wires the (injected) compositor to persistence — for each page it
    builds the placements (`page_placements`: each unit's *selected* translation over the union box of its
    regions, styled by the leading region type → preset; user-selected text wins, I-3/FR-29), composites onto
    the page's masked base (falling back to the original if masking hasn't run), writes
    `renders/<page>.render.png`, and records a `RenderArtifact` (`kind="render"`) tracing the render to its
    page (I-2). A per-page signature folds the base layer's signature and a placements fingerprint, so an
    unchanged page skips (NFR-8) while a re-mask or re-translation invalidates the render; a recompute drops
    the prior render first. `BBox.union` was added to combine a unit's region boxes.
  - `mfo.storage.export`: `export_pages` bundles a portable export directory — the translated page images
    (render → masked → original fallback so the whole volume is covered), the full source → OCR → translation
    `mapping.json` (FR-43), a machine-readable `manifest.json`, and a human-readable `transcript.txt`. Output
    is ordered by page index and otherwise deterministic (NFR-26).
  - CLI: bare `mfo export` now composites the selected translations onto the masked pages and writes the
    export bundle (pages + mapping + manifest + transcript), reporting any overflow; `--mapping` still emits
    the mapping alone. A `composite` pipeline stage joins `mfo run` once both rendering (masking) and
    translation are configured (it depends on both), and `mfo status` now reports masked vs composited pages
    on separate lines.
  - Tests: pure compositor (text painted into its box, base not mutated, overflow flagged, determinism,
    `composite_file` PNG bytes + read-only base + byte stability); storage (`page_placements` builds one per
    translated unit with the right preset/box, `composite_pages` writes a render traced to the page with the
    original untouched, fallback to original without a mask, idempotent then invalidated by a changed
    selection, `export_pages` bundles pages/mapping/manifest/transcript, original fallback when unrendered,
    determinism); CLI (`mfo export` composites + writes the bundle, the composite stage joins the pipeline
    only once render *and* translation are configured).
  - Satisfies: FR-14, FR-34, FR-43, MVP-9; I-1, I-2, I-3, I-4; NFR-8, NFR-26; spec §7.6, §10.8, §21.
- **Batch 5.2 — Font fitting & placement** (M5 Rendering & Export):
  - `mfo.render.typeset`: a pure, storage-free typesetting engine. `fit_text` finds the largest font size at
    which a translated string — greedily wrapped to the box width (`wrap_text`, hard-breaking any word too wide
    to fit) — still fits the padded bounding box, shrinking from the preset's ceiling to its floor (FR-34); the
    fit is readability-first and best-effort, so when even the smallest size overflows it emits that size and
    flags `overflow` rather than failing, keeping a too-small bubble graceful and the uncertainty visible
    (NFR-3, I-4). `render_layout` paints the fitted `TextLayout` onto a transparent RGBA tile the size of the
    box — vertically centred, horizontally aligned per the preset, stroke drawn under the fill — ready for the
    compositing batch to paste onto a masked page; `typeset` is the fit + render convenience.
  - Style presets (FR-35): named `StylePreset`s (`default`, `shout`, `whisper`, `caption`) bundle font, size
    range, line spacing, alignment, padding, fill, and stroke/outline, looked up via `get_preset`/`preset_names`.
    Font loading goes through an injectable `FontLoader` adapter (NFR-17); the default `load_font` is the offline
    provider (Pillow's built-in TrueType-backed default, or any named `font_path`), so the core path needs no
    font download (I-7/I-8). The fit search and rendering are pure functions of their inputs, so the same
    text + box + preset yield byte-identical output (NFR-26).
  - This batch is the pure layout/rendering engine; wiring it into the render stage to typeset selected
    translations onto the masked layer and export pages lands in batch 5.3.
  - Tests: large size chosen in a roomy box, wrapping to width with no horizontal/vertical overflow, overflow
    flagged in a tiny box, overlong-word hard-break, box-sized tile with visible in-bounds text, default
    outline paints stroke pixels while `whisper` paints none, alignment shifts text horizontally, fit + render
    determinism, preset registry consistency, font-loader caching.
  - Satisfies: FR-34, FR-35, NFR-3; NFR-26; SG-6 groundwork; spec §10.8.
- **Batch 5.1 — Text masking / removal** (M5 Rendering & Export):
  - `mfo.render.mask`: a pure, storage-free imaging module that removes source text from a page. Given the
    page and its detected region boxes it produces a **masked** layer — each box filled with its estimated
    local background colour (`estimate_background` takes the median of a ring sampled *outside* the box) so
    coloured/screentoned bubbles are reconstructed rather than punched white (FR-31/32) — and a 1-channel
    **mask** layer recording exactly which pixels changed. Masking is strictly confined to the region boxes,
    so line art and texture outside them stay byte-identical (FR-33). `restore` reverses a masking from the
    masked + mask layers (I-6); the source image is opened read-only and never mutated (I-1). Output is
    deterministic (NFR-26).
  - `mfo.storage.render`: `mask_pages` orchestrates persistence — reads each page's original (read-only),
    writes `renders/<page>.masked.png` + `renders/<page>.mask.png`, and records a `RenderArtifact`
    (`kind="mask"`) linking the masked layer back to its page (I-2). The imaging callable is injected so
    storage stays image-free. A per-page signature folds the source image, mask config, and a region
    fingerprint, so unchanged pages skip (NFR-8) and a re-detection invalidates the mask; a recompute drops
    the prior artifact and its files first (idempotent). Region-less pages still get a masked base.
  - CLI: `mfo render` masks every page into the reversible masked layer (offline; `--pad`/`--border`/`--force`)
    and persists its config so `mfo run` reproduces it; the render stage joins the pipeline once configured,
    depending only on detected regions (independent of OCR/translation). `mfo status` now lights the render
    line.
  - Tests: pure masking (text removed within region, line art outside preserved byte-for-byte, background
    reconstruction uses the local colour, ring-median estimate, `restore` recovers the original exactly,
    empty-regions no-op, determinism); storage stage (writes masked+mask PNGs traced to page, original
    untouched, region-less page copied, idempotent-then-forced, re-detection invalidation); CLI (`render`
    masks + status reports, render joins the pipeline once configured).
  - Satisfies: FR-31, FR-32, FR-33, I-1, I-6; NFR-8, NFR-26; spec §10.8, §21.
- **Batch 4.3 — Traceability & mapping export** (M4 Translation):
  - `mfo.core.traceability`: pure helpers resolving a unit's *selected* translation — `selected_candidate`
    returns the candidate the unit currently points at (or `None`), `selected_text` the text that would be
    rendered (empty when unselected). One place for the "which translation wins" rule that both the mapping
    export and the render stage consume (FR-26).
  - `mfo.storage.edits`: `EditRecord` scaffolding — `record_edit` appends an immutable, append-only change
    record for a unit; `list_edits` returns them oldest-first (optionally per unit). Human (and later
    auto-applied) edits stay auditable and reversible (I-3), the foundation the review editor (M6) builds on.
  - `mfo.storage.mapping`: `build_mapping` assembles the full **source → OCR → translation → edit** link
    graph keyed on stable entity IDs (FR-41) — for every translation unit, its page, the source bounding box
    + OCR text of each region, the unit's translation history (candidates) and applied edits, and its
    selected translation (FR-42). `write_mapping` dumps it to UTF-8 JSON (FR-43). Output is deterministic
    (pages by index, units by reading order, regions in stored order) so the same project yields a
    byte-stable mapping (NFR-26).
  - `mfo export --mapping` writes the mapping to `exports/mapping.json` (or `--out <dir>/mapping.json`),
    tracing every output region back to its source (DoD). Bare `mfo export` (page render) remains a 5.3
    placeholder.
  - Tests: core traceability (selected candidate/text incl. unselected + bare unit); storage edits
    (append-only persistence, oldest-first ordering, per-unit filtering); mapping (every region traced to
    page/bbox/OCR, translation history preserved, edit history included, selected-candidate reflected,
    UTF-8 JSON round-trip); CLI `export --mapping` (default + custom out dir) and the bare-export placeholder.
  - Satisfies: I-2, I-6; FR-26 (data), FR-41, FR-42, FR-43; MVP-7; NFR-26; spec §7.6, §21.
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
  is off the MVP-critical path. M4 landed 4.1 (translation adapter + context builder), 4.2 (glossary,
  terminology & style), and 4.3 (traceability & mapping export, MVP-7) — completing M4's MVP scope. The
  optional **batch 4.4 — API translation adapter** (opt-in cloud/LLM translation behind explicit config,
  never default) has since landed off the MVP-critical path. **M5 (Rendering & Export) is complete** — 5.1
  (text masking / removal — the reversible masked base layer), 5.2 (font fitting & placement — the pure
  typesetting engine), and 5.3 (composite & export pages — typesetting the selected translation onto each
  masked layer and exporting translated pages + mapping/manifest/transcript, MVP-9). **M6 (Review Editor)
  complete** — batch 6.1 (review backend/API: the framework-free review service + a FastAPI shell serving
  and mutating project state, edits persisted as records), batch 6.2 (local web editor UI: `mfo review`
  launches a dependency-free SPA — page list, zoom/pan canvas with clickable region overlays, side panel of
  OCR/translation/candidates/history/confidence, keyboard navigation, dark mode), and batch 6.3 (in-place
  editing & region ops: edit translations in place, revert candidates, status flags, move/resize, split/merge,
  manual reading-order, a low-confidence-first review queue, and a re-rendered page preview). **With M0–M6
  complete, the MVP Definition of Done (§21) is satisfied** — a user can import a folder of pages, detect &
  OCR, get context-aware translations, review/edit in place, export, and reopen later with all mappings
  intact. The optional ML detector (batch 2.2 — a lazy, fallback-guarded ONNX detector behind the `detect`
  extra) and optional panel detection (batch 3.3 — best-effort X–Y-cut panel boundaries refining reading
  order via `mfo order --panels`, cleanly disabled by default), and the optional API translation adapter
  (batch 4.4 — opt-in cloud/LLM translation via `mfo translate --translator api`, env-configured and never
  default) have since landed off the critical path. **All planned optional batches are now complete**; the
  remaining work is the post-MVP milestones M7 (AI Refinement) and M8 (Hardening & Stretch).

<!--
Template for a landed batch:

## [0.x.y] — YYYY-MM-DD
### Batch N.M — <title>
- <what changed>
- Satisfies: <FR-/NFR-/I-/MVP- IDs>
-->
