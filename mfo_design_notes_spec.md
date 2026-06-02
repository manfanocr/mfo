# mfo — Manhua Fanyi OCR / Translation Design Notes

**Working title:** mfo
**Meaning:** *manfanocr* / *ManfanOCR* — manga/manhua OCR + translation pipeline

---

## 1. Overview

mfo is an open-source desktop-first tool for processing manga volumes from a directory of page images, extracting text from speech bubbles and other text regions, reconstructing reading order, translating text while preserving context, and placing the translated text back onto the page with minimal manual effort.

The core design goal is to combine:

- **Reliable OCR** for Japanese by default, while supporting other scripts/languages.
- **Manga-aware layout analysis** for speech bubbles, SFX, notes, captions, and vertical text.
- **Context-preserving translation** that operates on dialogue groups, not isolated lines.
- **Editable final touches** so users can refine translations in situ.
- **Traceability** between original text regions and translated output.
- **Open-source friendliness** so the project is easy to extend, test, and run locally.

mfo should support both:

1. **Fully automatic mode** — OCR → ordering → translation → text replacement → export.
2. **Interactive review mode** — OCR → ordering → translation suggestions → human editing → final render.

---

## 2. Product vision

The product vision is expressed as numbered items for easy cross-reference.

- **G-1** mfo should become a strong open-source manga translation workstation that feels precise, modular, and trustworthy.
- **G-2** The tool should be good at reading messy manga layouts.
- **G-3** The tool should be good at grouping text correctly by bubble and conversation flow.
- **G-4** The tool should be good at preserving the nuance of dialogue.
- **G-5** The tool should make final text placement easy to adjust.
- **G-6** The tool should keep the user in control when automation is uncertain.
- **G-7** The tool should combine OCR, translation, and AI-assisted refinement without becoming a black box.

## 3. Primary goals

- **G-8** Accept a directory of page images as input.
- **G-9** Detect and OCR all meaningful text regions.
- **G-10** Reconstruct reading order appropriate for manga and related formats.
- **G-11** Group text into logical dialogue units.
- **G-12** Translate in bulk using context across the page, chapter, or volume.
- **G-13** Preserve links from each original region to its translated text and edits.
- **G-14** Generate output images with translated text inserted into the original page.
- **G-15** Provide an interactive editor for manual final touches.
- **G-16** Expose a clean architecture that can use different OCR, translation, and rendering backends.
- **G-17** Remain fully usable offline where possible.

## 4. Non-goals

The first version should not try to solve everything. These are tracked as numbered non-goals.

- **NG-1** Perfect translation quality for every language pair.
- **NG-2** Full automatic comic understanding at human level.
- **NG-3** Training a proprietary model from scratch.
- **NG-4** Supporting every archive format on day one.
- **NG-5** Cloud-only workflows.
- **NG-6** Mandatory internet access.
- **NG-7** “One-click perfection” without review options.

## 5. Invariants

The following properties should remain true throughout the system design and implementation.

- **I-1** The original source images remain available and are never silently destroyed.
- **I-2** Every OCR region, translation unit, and output placement must be traceable back to its source region.
- **I-3** User edits must take precedence over automated suggestions.
- **I-4** Confidence and uncertainty must remain visible rather than hidden.
- **I-5** Pipeline stages must be inspectable, restartable, and cacheable.
- **I-6** The tool should preserve a reversible link between source text and rendered output whenever feasible.
- **I-7** Optional AI assistance must not be required for the core workflow.
- **I-8** Local-only processing must remain possible.

---

## 6. Target users

### 6.1 Casual translators
Need a simple workflow to process a volume and export readable pages quickly.

### 6.2 Fan scanlation teams
Need batch processing, collaborative review, editable output, consistent styling, and traceability.

### 6.3 Power users / researchers
Need modular internal stages, logs, metadata, confidence scores, and reproducible outputs.

### 6.4 Open-source contributors
Need a codebase that is easy to run locally, test, extend, and replace components within.

---

## 7. Core workflow

### 7.1 Input
- User selects a directory of page images.
- Supported page formats should include common image types such as PNG, JPG, WEBP, and possibly TIFF.
- Optional metadata can be supplied:
  - source language,
  - target language,
  - volume title,
  - chapter number,
  - reading direction,
  - preferred translation style.

### 7.2 Analysis
- Detect text regions.
- Classify region types.
- OCR each region.
- Estimate reading order.
- Group regions into dialogue chains or text units.
- Produce confidence scores.

### 7.3 Translation
- Translate groups with context.
- Preserve names, honorifics, speech style, and recurring terminology.
- Use glossary and memory where available.
- Generate one or more candidate translations if AI assistance is enabled.

### 7.4 Placement
- Remove original text or mask it safely.
- Fit translated text into the available region.
- Render the text back onto the page.
- Preserve the look and feel of the original page as much as possible.

### 7.5 Review
- Let the user inspect each page.
- Show original region, OCR text, translation, and final rendering together.
- Allow manual edits to translation and layout in place.
- Flag low-confidence regions for human attention.

### 7.6 Export
- Export processed pages.
- Save project state.
- Save a structured mapping between source regions and translated output.
- Optionally export a transcript, subtitle-like text file, or JSON manifest.

---

## 8. Functional requirements

The functional requirements are numbered for easy reference in implementation and review.

### 8.1 Input and project handling
- **FR-1** The system must accept a directory of page images as input.
- **FR-2** The system should support configurable page ordering by filename, numeric sort, or embedded manifest.
- **FR-3** The system should keep the original images untouched unless the user explicitly opts into destructive processing.
- **FR-4** The system should store all derived data in a project directory.
- **FR-5** The system should support resuming an interrupted run.

### 8.2 Language support
- **FR-6** The system must support Japanese by default.
- **FR-7** The system should be designed for other languages/scripts as well.
- **FR-8** The system should allow the source language to be auto-detected or user-specified.
- **FR-9** The system must allow the target language to be user-specified.

### 8.3 OCR
- **FR-10** The system must detect text regions in manga pages.
- **FR-11** The system must OCR speech bubbles, narration boxes, side text, captions, SFX, and other visible text types where possible.
- **FR-12** The system should preserve per-region OCR confidence.
- **FR-13** The system should support vertical text.
- **FR-14** The system should support curved or stylized text as a best-effort feature.
- **FR-15** The system should store OCR output separately from translation output.

### 8.4 Layout and reading order
- **FR-16** The system must infer reading order for manga layouts.
- **FR-17** The system should support right-to-left and top-to-bottom ordering.
- **FR-18** The system should detect page-level and panel-level structure when useful.
- **FR-19** The system should group related regions into conversation chains.
- **FR-20** The system should allow manual correction of reading order.

### 8.5 Translation
- **FR-21** The system must translate OCR text into the chosen target language.
- **FR-22** The system should translate with page/chapter context, not just isolated lines.
- **FR-23** The system should preserve names, terminology, and tone consistently.
- **FR-24** The system should support glossary injection.
- **FR-25** The system should support style options such as literal, balanced, natural, and localized.
- **FR-26** The system should allow the user to compare source text, raw translation, and edited final text.

### 8.6 AI-assisted refinement
- **FR-27** The system should optionally use AI to improve human readability before final placement.
- **FR-28** The AI layer should suggest more natural phrasing, shorter alternatives for tight bubbles, speaker-consistent tone, punctuation cleanup, emphasis handling, and ambiguity notes.
- **FR-29** The AI layer should never silently overwrite user-approved text.
- **FR-30** The AI layer should expose confidence and rationale when practical.

### 8.7 Text removal and replacement
- **FR-31** The system must support masking or removing original text before placing translations.
- **FR-32** The system should support automatic background reconstruction where possible.
- **FR-33** The system should preserve line art and important texture as much as feasible.
- **FR-34** The system should support text scaling, wrapping, alignment, and placement within bubbles.
- **FR-35** The system should support font selection, stroke/outline control, and style presets.

### 8.8 Interactive editor
- **FR-36** The system should provide a page preview with selectable text regions.
- **FR-37** The user should be able to edit translations in place.
- **FR-38** The user should be able to adjust font size, line breaks, alignment, and region position.
- **FR-39** The user should be able to split or merge detected regions.
- **FR-40** The user should be able to mark regions as correct, needs review, ignore, or manual transcription required.

### 8.9 Traceability
- **FR-41** Every translated region must keep a stable ID.
- **FR-42** Every output text region must link back to source page, source bounding box, OCR text, translation history, and user edits.
- **FR-43** The system should be able to export these mappings in JSON.

### 8.10 Batch processing and automation
- **FR-44** The system must support batch processing of many pages.
- **FR-45** The system should support headless mode for automation.
- **FR-46** The system should support command-line execution for power users.
- **FR-47** The system should support configuration files for repeatable runs.

### 8.11 Project persistence
- **FR-48** The system must persist intermediate results.
- **FR-49** The system should allow users to revisit and revise prior decisions.
- **FR-50** The system should version project metadata and edits.

## 9. Non-functional requirements

The non-functional requirements are also numbered for stable cross-referencing.

### 9.1 Quality and accuracy
- **NFR-1** OCR quality should be high enough for practical manga translation workflows.
- **NFR-2** Translation quality should prioritize readability and context.
- **NFR-3** Layout reconstruction should minimize overlaps and visual artifacts.
- **NFR-4** The system should expose confidence so users know where to review.

### 9.2 Performance
- **NFR-5** The system should process pages in reasonable time on a modern desktop.
- **NFR-6** The system should support parallelism where safe.
- **NFR-7** The system should cache expensive intermediate outputs.
- **NFR-8** The system should avoid recomputing unchanged stages.

### 9.3 Reliability
- **NFR-9** The system should handle malformed images gracefully.
- **NFR-10** The system should not lose user edits on crash or interruption.
- **NFR-11** The system should save incremental progress frequently.
- **NFR-12** The system should provide useful error messages.

### 9.4 Usability
- **NFR-13** The workflow should be understandable to non-experts.
- **NFR-14** The editor should be precise enough for scanlation work.
- **NFR-15** UI controls should be minimal but powerful.
- **NFR-16** Keyboard shortcuts should be available for repetitive actions.

### 9.5 Extensibility
- **NFR-17** OCR, translation, and rendering backends should be swappable.
- **NFR-18** The processing pipeline should be modular.
- **NFR-19** Plugin hooks should be possible for custom heuristics or models.

### 9.6 Portability
- **NFR-20** The tool should run on major desktop operating systems if feasible.
- **NFR-21** The tool should support CPU-only mode.
- **NFR-22** GPU acceleration should be optional.

### 9.7 Privacy
- **NFR-23** The tool should support fully local processing.
- **NFR-24** Network calls should be optional and user-controlled.
- **NFR-25** Sensitive source pages should not leave the user’s machine by default.

### 9.8 Reproducibility
- **NFR-26** The same input and configuration should produce traceable output.
- **NFR-27** The project should record versions of models, rules, and configuration.

### 9.9 Maintainability
- **NFR-28** The codebase should be readable and strongly structured.
- **NFR-29** Critical stages should be covered by tests.
- **NFR-30** The data model should be explicit and stable.

## 10. Proposed pipeline

### 10.1 Stage 1 — Import
- Scan input directory.
- Sort pages.
- Create project manifest.

### 10.2 Stage 2 — Preprocessing
- Normalize image size and color space.
- Optionally deskew.
- Optionally denoise.
- Detect page orientation.

### 10.3 Stage 3 — Region detection
- Detect text candidates.
- Detect bubble boundaries.
- Detect narration boxes.
- Detect SFX and small side text.
- Detect panel boundaries if needed for reading order.

### 10.4 Stage 4 — OCR
- Run OCR per region.
- Store character-level or word-level results if available.
- Store confidence and alternative hypotheses.

### 10.5 Stage 5 — Structure inference
- Infer reading order.
- Group text by bubble and scene flow.
- Build conversation chains.
- Estimate speaker continuity where possible.

### 10.6 Stage 6 — Contextual translation
- Translate a batch of linked regions together.
- Preserve dialogue context.
- Normalize terminology.
- Apply glossary and style rules.

### 10.7 Stage 7 — Human-readable refinement
- Use AI to suggest tightened, natural, and bubble-fit variants.
- Produce final editable candidate text.
- Flag ambiguity and low confidence.

### 10.8 Stage 8 — Text rendering
- Remove or mask source text.
- Render translated text into bubble regions.
- Fit text by wrapping, resizing, or rephrasing if necessary.

### 10.9 Stage 9 — Review and finalize
- User edits pages interactively.
- User approves or rejects translations.
- System exports final pages and project records.

---

## 11. Data model

A project should be represented using explicit entities.

### 11.1 Core entities

- **Project**
  - id
  - name
  - source language
  - target language
  - created time
  - config
  - model/version references

- **Page**
  - id
  - index
  - image path
  - dimensions
  - preprocessing metadata

- **Region**
  - id
  - page id
  - bounding box / polygon
  - region type
  - reading order index
  - confidence

- **OCRSpan**
  - id
  - region id
  - recognized text
  - confidence
  - token offsets if available

- **TranslationUnit**
  - id
  - ordered region ids
  - source text bundle
  - context bundle
  - translation candidates
  - selected translation

- **EditRecord**
  - id
  - translation unit id
  - before text
  - after text
  - editor action
  - timestamp

- **RenderArtifact**
  - id
  - page id
  - output path
  - render parameters

### 11.2 Suggested persistence format
- Human-readable metadata in JSON or YAML.
- Large or relational data in SQLite.
- Output caches stored in project subdirectories.

---

## 12. AI integration design

AI should be a **helper layer**, not the foundation of correctness.

### 12.1 Good uses of AI
- Resolve awkward or fragmented OCR into natural English.
- Merge broken bubble text into fluent dialogue.
- Suggest context-sensitive translations.
- Suggest line breaking for tight spaces.
- Detect likely speaker shifts.
- Propose style-consistent alternatives.
- Identify regions that deserve human review.

### 12.2 Bad uses of AI
- Blindly overwriting user-edited translations.
- Hiding uncertainty.
- Making untraceable global changes.
- Reordering text without visibility.

### 12.3 AI outputs should include
- candidate translation,
- optional literal translation,
- readability rewrite,
- confidence estimate,
- explanation or rationale when useful,
- warnings for uncertainty.

### 12.4 AI modes
- **Assist mode:** provide suggestions only.
- **Review mode:** highlight best candidate and explain alternatives.
- **Auto mode:** apply high-confidence suggestions automatically, but preserve auditability.

### 12.5 Context sources for AI
- nearby text regions,
- page context,
- chapter context,
- glossary / terminology list,
- character name memory,
- user style preferences.

---

## 13. UI / UX specifications

### 13.1 Main screen
- Project browser.
- Page list.
- Pipeline status.
- Error and warning summary.

### 13.2 Page editor
- Central image canvas.
- Clickable detected regions.
- Side panel with:
  - OCR text,
  - translation,
  - edit history,
  - confidence,
  - region metadata.
- Keyboard navigation between regions.

### 13.3 In-place editing
- Double-click text to edit.
- Drag to reposition text box.
- Resize text box.
- Auto-fit button.
- One-click “suggest alternative” button.

### 13.4 Review workflow
- Show all low-confidence pages first.
- Allow accept/reject per region.
- Let users batch-apply styles or glossary terms.

### 13.5 Accessibility and ergonomics
- Keyboard shortcuts.
- Zoom and pan.
- Dark mode.
- Clear contrast on overlays.
- Optional high-DPI support.

---

## 14. Architecture notes

### 14.1 Suggested modular layers

1. **Core domain layer**
   - data models,
   - project state,
   - pipeline orchestration.

2. **Vision layer**
   - region detection,
   - OCR adapters,
   - layout analysis.

3. **Language layer**
   - translation engine adapters,
   - glossary,
   - context builder,
   - AI rewrite and review assistance.

4. **Rendering layer**
   - font fitting,
   - text placement,
   - image compositing,
   - bubble-aware styling.

5. **UI layer**
   - page viewer,
   - editor,
   - batch controls,
   - project settings.

6. **Persistence layer**
   - project files,
   - caches,
   - metadata,
   - export formats.

### 14.2 Recommended design principle
Every processing stage should be:
- inspectable,
- restartable,
- cacheable,
- replaceable.

### 14.3 Backends and adapters
The system should not hard-code any single OCR or translation provider. It should support adapters such as:
- local OCR engine,
- local or remote translation engine,
- local LLM assistant,
- optional cloud services.

---

## 15. Recommended file structure

```text
mfo/
  docs/
  models/
  presets/
  samples/
  src/
    core/
    vision/
    language/
    render/
    ui/
    storage/
    cli/
  tests/
  project-template/
```

A project instance can contain:

```text
project-name/
  manifest.json
  pages/
  cache/
  regions/
  ocr/
  translations/
  renders/
  exports/
  logs/
```

---

## 16. Suggested MVP scope

The first usable version should include these numbered MVP items:

- **MVP-1** Directory import.
- **MVP-2** Page ordering.
- **MVP-3** Text region detection.
- **MVP-4** OCR for Japanese.
- **MVP-5** Basic manga reading order.
- **MVP-6** Bulk translation.
- **MVP-7** Mapping from source region to translation.
- **MVP-8** A simple in-place review editor.
- **MVP-9** Export of translated pages.
- **MVP-10** Project save/resume.
- **MVP-11** Confidence-based highlighting.

MVP should not require perfect bubble removal or perfect AI dialogue understanding.

## 17. Stretch goals

- **SG-1** Panel-aware context passing.
- **SG-2** Character name memory across volumes.
- **SG-3** Terminology database with team sharing.
- **SG-4** Translation style presets per series.
- **SG-5** SFX detection and optional transliteration.
- **SG-6** Speech bubble shape-aware text wrapping.
- **SG-7** OCR correction suggestions using language models.
- **SG-8** Collaborative review workflow.
- **SG-9** Plug-in marketplace.
- **SG-10** Web-based review mode for local network use.

## 18. Quality bar for “best-in-class” open source status

To stand out among open-source manga translation tools, mfo should feel:

- **precise** in region handling,
- **transparent** in every transformation,
- **editable** at every step,
- **context-aware** in translation,
- **reliable** under batch workloads,
- **modular** for contributors,
- **private by default**,
- **pleasant to use** for both automatic and manual workflows.

The project should treat translation as an iterative human-AI collaboration rather than a black box.

---

## 19. Open questions

- Which OCR backend gives the best balance of accuracy, speed, and maintainability?
- Should the default UI be desktop-native, browser-based local app, or hybrid?
- Should the translation engine be plugin-driven from the start?
- How much panel analysis is needed for a good first release?
- Should final text fitting prioritize exact visual fidelity or maximum readability?
- How much should the system attempt automatic text removal vs. user-guided cleanup?

---

## 20. Implementation notes for Claude Code

This project should be easiest to build when split into small, testable increments.

Recommended approach:

1. Define the project data model and manifest.
2. Implement image import and page ordering.
3. Add region detection and OCR.
4. Add a canonical region/translation mapping layer.
5. Add translation adapters.
6. Add reading order and grouping heuristics.
7. Add rendering and text fitting.
8. Add interactive review tools.
9. Add caching, resume, and exports.
10. Add AI-assisted refinement and confidence-based review.

Suggested engineering rules:
- keep stage outputs serializable,
- keep all intermediate data inspectable,
- make all model calls optional and swappable,
- never lose the original text mapping,
- prefer explicit data structures over hidden state.

---

## 21. Definition of done

mfo is “done” for a first major release when a user can:

1. point it at a directory of manga pages,
2. detect and OCR text regions,
3. obtain context-aware translations,
4. review and edit translations in place,
5. export translated pages,
6. revisit the project later with all mappings preserved.

---

## 22. Final design principle

The best version of mfo should not merely translate manga.
It should preserve the relationship between **source text**, **context**, **human intent**, and **final readable page output**.

That traceability is the feature that makes the whole system trustworthy.
