# mfo — Data Model & Persistence

Concrete form of spec §11. The data model is **explicit and stable** (NFR-30) and exists to
guarantee **traceability** (I-2, I-6, FR-41-43): every rendered region links back to its source.

## Entities

All entities carry a **stable, opaque ID** (ULID — sortable + unique). IDs never change once
assigned; they are the backbone of traceability.

### Project
`id · name · source_lang · target_lang · created_at · config · model_versions`
Top-level container. `model_versions` records the OCR/translate/render backend versions used,
for reproducibility (NFR-26/27).

### Page
`id · project_id · index · image_path · width · height · preprocessing`
One source image. `image_path` points at the **read-only original** (I-1). `preprocessing`
holds derived-image metadata (cache refs, deskew/orientation), never overwriting the original.

### Region
`id · page_id · bbox|polygon · type · reading_order_index · confidence · status`
A detected text area. `type` ∈ {bubble, narration, sfx, caption, side_text, unknown}.
`status` ∈ {auto, correct, needs_review, ignore, manual} (FR-40). `reading_order_index` set by
the structure stage (FR-16) and user-overridable (FR-20).

### OCRSpan
`id · region_id · text · confidence · alternatives · token_offsets?`
OCR output, stored **separately** from translation (FR-15). Keeps confidence (FR-12) and
alternate hypotheses when the engine provides them.

### TranslationUnit
`id · ordered_region_ids · source_bundle · context_bundle · candidates · selected · style`
A dialogue/logical unit grouping ≥1 region (FR-19). Translated **with context** (FR-22);
`candidates` may include literal/natural/AI variants (spec §12.3); `selected` is the active one.

### EditRecord
`id · translation_unit_id · before · after · action · editor · timestamp`
Append-only audit log of human (and auto-applied AI) changes. Enables "edits win" (I-3) and
lets the UI show edit history (FR-26, §13.2). Never deleted — corrections are new records.

### RenderArtifact
`id · page_id · output_path · params`
A produced page render with the exact parameters used (font, fit, mask settings) for
reproducibility.

## Relationships

```
Project 1─┬─* Page 1─* Region 1─* OCRSpan
          │                 └────────────┐
          └─* TranslationUnit *──────────┘ (ordered_region_ids)
                   │
                   └─* EditRecord
Page 1─* RenderArtifact
```

A `TranslationUnit` references regions across one (or, for chains, adjacent) pages. The
**reverse links** (Region → which unit, unit → which OCR) are what `mfo export --mapping`
serializes (FR-43).

## Persistence (spec §11.2)

- **`manifest.json`** — human-readable project header: id, name, langs, config, model versions,
  page order. Easy to diff and inspect.
- **SQLite (`project.db`)** — relational + high-churn data: regions, OCR spans, translation
  units, edit records, render artifacts. Indexed by `page_id` / `region_id` for fast review.
- **Cache dirs** — content-hashed intermediate outputs (preprocessed images, masks, detector
  outputs) for skip-if-unchanged (NFR-7/8/26).

Writes are **atomic** (temp file + rename) and progress is flushed incrementally so a crash
never corrupts state or loses approved edits (NFR-10/11).

## Project directory layout (spec §15)

```
project-name/
  manifest.json        # human-readable header
  project.db           # SQLite: regions, OCR, translations, edits, renders
  pages/               # references/symlinks to originals (never modified)
  cache/               # hashed intermediates (preprocess, detect, masks)
  regions/             # optional per-page region debug dumps (JSON)
  ocr/                 # optional OCR debug dumps (JSON)
  translations/        # optional translation debug dumps (JSON)
  renders/             # composited output pages
  exports/             # final pages + JSON mapping + transcripts
  logs/                # structured run logs
```

The `*/` JSON dumps mirror DB rows for inspectability (I-5); the DB is the source of truth.

## ID & traceability guarantees

1. IDs are assigned once and are immutable.
2. Deleting a region tombstones it (keeps history) rather than dropping links.
3. Every `RenderArtifact` is reconstructable from its `TranslationUnit` → `OCRSpan` → `Region`
   → `Page` chain (I-6).
4. The exported JSON mapping is sufficient to answer "where did this translated line come
   from?" for any output region (FR-42).
