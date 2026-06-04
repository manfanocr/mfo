# mfo user guide — engines & providers

mfo runs **fully offline by default** (I-7/I-8): the baseline detector, manga-ocr, and Argos
translation need no network at run time. Everything below is about *swapping in* a different
detector, OCR engine, or translator — each is a pluggable adapter selected by name, and the cloud
ones are strictly opt-in and configured **only** from environment variables (no endpoint or key is
ever written into the project).

The pipeline stages, end to end:

```
mfo init <proj> --source ja --target en
mfo import <proj> <folder-of-pages>
mfo detect <proj>            # → text regions
mfo order <proj>             # → reading order
mfo group <proj>             # → translation units (one per bubble; see below)
mfo ocr <proj>               # → recognized text
mfo translate <proj>         # → translations
mfo render <proj>            # → mask source text
mfo export <proj>            # → composited pages + mapping
mfo review <proj>            # → open the web editor
```

`mfo run <proj>` runs all configured stages; `mfo status <proj>` shows progress.

---

## Importing pages (folders & CBZ/ZIP)

`mfo import` accepts either a **folder** of page images or a **`.cbz`/`.zip` archive**:

```
mfo import <proj> chapter-01/         # a folder of .png/.jpg/.webp/.tiff
mfo import <proj> chapter-01.cbz      # a comic archive (CBZ is just a ZIP of images)
```

Pages are ordered by the same natural sort in both cases (`2` before `10`); pass `--order name`
for plain lexicographic order, or `--manifest <file>` to fix an explicit order. Archive images are
extracted **read-only** into the project cache and then copied into `pages/` — the source archive
itself is never modified (I-1). Non-image entries (`ComicInfo.xml`, macOS resource forks) are
ignored; a corrupt entry or a duplicate filename is skipped with a warning rather than aborting the
whole import.

> **CBR/RAR is not supported** — RAR needs a non-free dependency mfo doesn't bundle. Re-pack a
> `.cbr` as `.cbz` (e.g. `unrar` then `zip`) or extract it to a folder first.

---

## Going faster: parallel pages (`--jobs`)

The heavy per-page stages — **preprocess, detect, OCR, translate, render, composite** — can process
several pages at once. Pass `--jobs N` (short `-j N`) to any of `detect`, `ocr`, `translate`,
`render`, `export`, and `run`:

```bash
mfo run <proj> --jobs 4        # 4 pages in flight at once
mfo ocr <proj> -j 0            # 0 = auto: one worker per CPU core (capped at 8)
```

`--jobs` is purely a speed knob: the result is **byte-identical** regardless of the worker count and
the per-page cache still skips unchanged pages (NFR-8) — it never changes *what* is produced, only
how fast. It helps most where the work is in native code or network I/O (OCR models, Argos/NLLB,
PIL, or an `api`/Ollama round-trip), which is exactly where these stages spend their time. Default
is `1` (serial).

### Benchmarking it

`mfo bench` force-re-runs each configured heavy stage and times it, so you can see the speedup on
*your* pages and pick a worker count:

```bash
mfo bench <proj> --jobs 1      # baseline
mfo bench <proj> --jobs 4      # compare
mfo bench <proj> --stage ocr -j 8   # just one stage
```

It uses an in-memory run state, so timing runs don't disturb the project's own pipeline progress.

---

## Translators (`mfo translate --translator …`)

| Name | Network? | Context-aware? | Configure with |
|------|----------|----------------|----------------|
| `argos` *(default)* | No (offline) | No | install once (below) |
| `deepl` | Yes | No | `MFO_DEEPL_API_KEY` |
| `api` | Yes | **Yes** (sends nearby dialogue) | `MFO_API_KEY`, `MFO_API_BASE_URL`, `MFO_API_MODEL` |

Only the translator *name* is saved to the project; keys and URLs live in the environment.

### `argos` — offline, the default

```bash
pip install 'mfo[translate]'
# install the language pair you need (one-time, downloads a model):
argospm update
argospm install translate-ja_en
mfo translate <proj>            # --translator argos is the default
```

If the pair isn't installed you get a clear error naming the package to install (not a cryptic
`NoneType … get_translation`).

### `deepl` — official API (has a free tier)

```bash
export MFO_DEEPL_API_KEY="your-key-here"      # free keys end in ':fx'
# free tier uses the default api-free host; for a Pro key set the pro endpoint:
# export MFO_DEEPL_API_URL="https://api.deepl.com/v2/translate"
mfo translate <proj> --translator deepl
```

Sends only the line of text (never the page image). DeepL has no slot for surrounding dialogue, so
it isn't context-aware — use `api` for that.

### `api` — any OpenAI-compatible endpoint (LLMs, gateways)

This is the **context-aware** path: each line is sent with its nearby dialogue, the page locator,
the requested style (`--style`), and any pinned glossary terms.

If you ordered the project panel-aware (`mfo order <proj> --panels`), that nearby-dialogue window
is **scoped to the bubble's own panel** — context no longer bleeds in from an adjacent frame (SG-1).
Offline adapters (`argos`/`deepl`) ignore context, so this only affects the `api`/LLM path.

```bash
export MFO_API_KEY="sk-…"
export MFO_API_BASE_URL="https://api.openai.com/v1"     # or any compatible gateway
export MFO_API_MODEL="gpt-4o-mini"
mfo translate <proj> --translator api --style natural
```

Point `MFO_API_BASE_URL` at any service that speaks the OpenAI chat-completions format — including
LLM gateways and proxies that wrap **DeepL or Google Translate** behind an OpenAI-compatible API.

#### Local LLM via Ollama (fully offline, no cloud key)

[Ollama](https://ollama.com) serves an OpenAI-compatible endpoint, so the `api` adapter drives a
**local** model with no data leaving your machine — a good fit for mfo's offline-first design.

```bash
ollama pull gemma3:12b                 # or a translation-tuned model, e.g. zongwei/gemma3-translator:4b
export MFO_API_BASE_URL="http://localhost:11434/v1"
export MFO_API_MODEL="gemma3:12b"
export MFO_API_KEY="ollama"            # any non-empty value: Ollama ignores it, but the adapter requires one
export MFO_API_TIMEOUT="240"           # the first request loads the model into RAM/VRAM — give it room
mfo translate <proj> --translator api --style natural
```

Notes:
- The adapter sends its **own** manga-translation system prompt, which overrides the `SYSTEM` block
  baked into a custom modelfile (e.g. `gemma3-translator`). Such models still work — they're general
  instruction models underneath — but their special prompt format is bypassed, so a plain `gemma3`
  base model performs comparably here.
- Context (nearby dialogue, glossary, style) is included in the prompt just like any `api` backend.

### What about "free Google Translate"?

There's no official *free* Google Translate API; the truly free path relies on **unofficial**
endpoints scraped from the consumer site, which violate Google's ToS and break without warning. mfo
deliberately does **not** bundle one. Your supported options for Google-quality output are:

- the official **Google Cloud Translation** API, reached through an OpenAI-compatible gateway via
  the `api` adapter above, or
- **DeepL**'s official free tier (`--translator deepl`).

---

## Glossary & cross-volume terminology memory (`mfo glossary …`)

A **glossary** pins how a recurring source term — a character name, honorific, place, or bit of
jargon — should read in the target, so it renders consistently everywhere (FR-23/24). Each project
has its own glossary:

```bash
mfo glossary add <proj> 太郎 Taro --alias Tarou   # pin 太郎 → "Taro"; normalize the variant "Tarou"
mfo glossary list <proj>
mfo glossary remove <proj> 太郎
```

Offline translators (`argos`/`deepl`) can't be *instructed*, so the glossary is **enforced** by
rewriting known variant spellings to the canonical target in the output; the `api`/AI path also gets
the applicable terms injected into its prompt.

### Sharing terms across volumes (SG-2, SG-3)

A series spans many volumes, each its own project. A **series glossary** is a shared store — one JSON
file outside any project — that several volumes link to, so a name settled in volume 1 carries into
volume 2 without re-entry:

```bash
# in each volume, link the same shared file:
mfo glossary series link vol1 ./series.json
mfo glossary series link vol2 ./series.json

# settle a term while working on vol1, then promote it into the shared store:
mfo glossary add vol1 太郎 Taro --alias Tarou
mfo glossary promote vol1 太郎

mfo glossary series list vol2          # vol2 now inherits 太郎 → Taro
```

A unit consults **project → series**: a project entry with the same source term **overrides** the
series default (the per-volume decision wins). For team sharing, the store round-trips losslessly:

```bash
mfo glossary series export vol1 ./share.json     # hand off to a teammate
mfo glossary series import vol2 ./share.json     # merge in (or --replace)
```

The shared store is consulted by `mfo translate` and `mfo run`; changing it re-translates affected
pages on the next run (the glossary is part of the translation cache key). Unlinked projects are
unaffected — the offline core path is unchanged (I-7). The review editor can also promote a term via
`POST /api/glossary/series/promote`.

---

## Per-series style presets (`mfo preset …`, SG-4)

A series' volumes should read and look the same. A **series preset** is a named bundle of the three
per-series decisions — the translation **style**, a link to the shared **series glossary**, and the
**render** (masking) knobs — kept in a single JSON store **outside** any project, so every volume can
adopt the same look in one step:

```bash
# Define a preset (in ./series-presets.json) for the whole series:
mfo preset save ./series-presets.json house \
    --style natural --glossary ./series.json --pad 3 --border 6
mfo preset list ./series-presets.json

# Apply it to a new volume — sets style, links the glossary, and sets render config at once:
mfo preset apply vol2 ./series-presets.json house

mfo preset remove ./series-presets.json house
```

`apply` writes the project's `translate.style`, `series_glossary`, and `render` config in one go
(preserving any translator you already chose). The preset store doubles as the portable export — it
round-trips losslessly, so a team can share one file. A project that never applies a preset resolves
exactly as before (the offline core is unaffected, I-7).

---

## Sound effects / SFX (`mfo sfx`, SG-5)

Onomatopoeia (ドーン, ばたん…) reads differently from dialogue — it's drawn art, often better
*transliterated* than translated, and sometimes best left as the original art. `mfo sfx` handles SFX
regions distinctly, **opt-in and fully offline by default**:

```bash
mfo sfx <proj> --mode transliterate   # romanize SFX (ドーン → "DOON") and typeset that
mfo sfx <proj> --mode skip            # leave the original SFX art untouched (no mask, no text)
mfo sfx <proj> --mode render          # (default) translate & typeset SFX like dialogue
```

It does two things: it **classifies** SFX regions (the offline `heuristic` classifier promotes a
large, stretched, non-bubble region to `sfx` — a detector's label and your manual edits always win),
and it attaches a **transliteration** as an `sfx` translation candidate to every SFX-led unit (the
offline `kana` transliterator; swap either via `--classifier` / `--transliterator`, or a plugin).
The transliteration candidate is always created so it's visible in review; only `--mode transliterate`
*selects* it, and never over a translation you've chosen by hand (I-3).

The `--mode` toggle is honored by `mfo render`, `mfo export`, and `mfo run`: in `skip` mode the mask
and composite stages leave SFX regions alone so the original art shows through. SFX handling joins
`mfo run` once both OCR and an SFX mode are configured; with no SFX configured, dialogue rendering is
exactly as before.

---

## Bubble-shape-aware text fitting (SG-6)

A round or oval bubble is widest across its middle and narrows toward the top and bottom, so text fit
to the bounding *box* can spill over the curved edge. When a region carries a bubble **outline** (its
`polygon`), `mfo render`/`mfo export` automatically fit the text to the bubble *shape* instead — each
line is wrapped to the polygon's interior width at that line's height, and the block is centred where
the bubble is widest. There's nothing to turn on: it happens whenever a region has a polygon (a
detector that emits outlines, or a hand-drawn region in the editor). Regions with only a box render
exactly as before, so nothing regresses.

---

## AI-assisted refinement (`mfo assist`)

An **optional** layer that uses an LLM to refine your translations — more natural phrasing, a
literal rendering, a readability rewrite, a shorter alternative for tight bubbles, a confidence
estimate, a rationale, ambiguity warnings, and speaker-shift hints (FR-27/28/30, §12). It is
**opt-in and off the core path**: nothing here runs unless you call `mfo assist`, it is *not* part
of `mfo run`, and the offline pipeline is unaffected (I-7). It reuses the same OpenAI-compatible
endpoint as the `api` translator — so a local Ollama model works here too — configured from
`MFO_AI_*` env vars, falling back to the `MFO_API_*` set:

```bash
# reuse your api/Ollama endpoint, or point AI review at a stronger model:
export MFO_API_BASE_URL="http://localhost:11434/v1"   # (or your OpenAI-compatible gateway)
export MFO_API_KEY="ollama"
export MFO_AI_MODEL="gemma3:12b"                       # MFO_AI_* overrides MFO_API_* for the AI layer
mfo translate <proj>            # produce the draft translations first
mfo assist <proj> --mode review # then refine them
```

Run it **after** `mfo translate` — it refines the existing draft, never the raw OCR alone. Three
modes (`--mode`, §12.4), in increasing autonomy:

| Mode | What it does to the selection |
|------|-------------------------------|
| `assist` *(default)* | Attaches AI suggestions as extra candidates; **never changes** which one is selected. |
| `review` | Also **highlights** (selects) the AI candidate as the recommended one. |
| `auto` | Also **applies** the AI candidate automatically — but only when its confidence ≥ `--min-confidence` (default `0.8`). |

In **every** mode the AI only *adds* candidates — it never overwrites text, and it never changes the
selection of a unit you've already edited by hand (a human/`manual` translation is left alone,
FR-29). Any selection change `review`/`auto` makes is recorded as an audit edit (visible in the
review editor's history), so it stays inspectable and reversible (I-3). Re-running is cached and
idempotent; pass `--force` to re-run, and use `mfo review` to compare the AI candidate, literal,
readability, and your own text side by side.

**Uncertainty surfaces in the review queue.** A unit whose AI suggestion is low-confidence is
flagged for review just like low-confidence OCR — its region rises to the top of the queue (with an
**AI** badge) even when the OCR itself was confident, and the AI's rationale (its reasoning,
ambiguity warnings, and speaker-shift hints) shows on the queue row and the candidate card, so you
always see *why* it was flagged (FR-30, I-4). The flag threshold matches the review queue's
confidence threshold.

---

## LLM OCR correction (`mfo ocr-correct`, SG-7)

When OCR misreads a character, an LLM can often guess the intended line. This **opt-in** path asks
one to propose corrected *readings* for low-confidence spans — and records them as **suggestions
only**, never overwriting the recognized text (I-3):

```bash
# Uses the same MFO_AI_* / MFO_API_* endpoint as `mfo assist` (text-only — never the page image):
mfo ocr-correct <proj> --threshold 0.5 --max-alternatives 3
```

Each low-confidence span's text is sent to the model; the proposed readings are appended to the
span's `alternatives`. In `mfo review`, the OCR section lists each alternative with a **Use** button —
one click adopts it as the OCR text (the previous text stays in the list, so it's reversible). It is
off the core path and **not** part of `mfo run`; a project that never runs it is unchanged (I-7).

---

## Collaborative review on a LAN (`mfo review --host …`, SG-8/SG-10)

By default `mfo review` binds to `127.0.0.1`, so only your machine can reach it. To let teammates on
the same network review one project together, bind to a LAN-reachable host and (recommended) require a
shared token:

```bash
# Share on the network with a token; reviewers open the printed /?token=… URL:
mfo review <proj> --host 0.0.0.0 --token "$(openssl rand -hex 16)"
```

The token is required on every API call (sent automatically by the editor); the page itself stays
loadable so a browser can pick the token up from the URL. Serving on a non-local host **without** a
token prints a warning — anyone on the network could then edit. mfo never reaches the public internet
for this; it's local-network only, private by default (NFR-23).

Once shared, concurrent review is safe:

- **Who edited what.** Set your name in the **reviewer** field (top bar); every edit you make is
  attributed to it in the edit log and the undo/redo history.
- **No silent overwrites.** Each page carries a revision; if two reviewers edit the same page, the
  second save is rejected with a clear *"page changed"* conflict and the editor reloads to the latest
  version so you can redo your change on top — approved work is never silently lost (I-3).
- **Claiming pages.** Use the **Claim page** button (or the page badges) to signal you're working on a
  page; others see who holds it and can take it over or you can release it when done.

Single-user localhost review is completely unchanged — no token, no claims, no conflicts.

---

## OCR engines (`mfo ocr --engine …`)

| Name | Languages | Install |
|------|-----------|---------|
| `manga-ocr` *(default)* | Japanese (incl. vertical) | `pip install 'mfo[ocr]'` |
| `paddleocr` | JP / ZH / EN / KO | `pip install 'mfo[ocr-paddle]'` |

```bash
mfo ocr <proj>                       # manga-ocr (best for Japanese manga)
mfo ocr <proj> --engine paddleocr    # PaddleOCR; uses the project's source language
```

Both load their model lazily on first use and report a clear, actionable error if the optional
dependency is missing. `paddleocr` picks its model from the project's `--source` language.

> **PaddleOCR needs the `paddlepaddle` backend, and that backend is Python-version-picky.**
> The `mfo[ocr-paddle]` extra targets PaddleOCR 3.x and pulls in `paddlepaddle`. PaddlePaddle ships
> wheels only for **CPython 3.8–3.13** (no 3.14+ build exists yet), distributed from its own index
> rather than PyPI. Installing `paddleocr` *without* a working `paddlepaddle` lets the engine import
> but not run; mfo reports this as a dependency error (OCR) or falls back to the baseline detector
> (detection) rather than crashing. To get a runnable stack, use a supported Python and install the
> backend from PaddlePaddle's index:
>
> ```bash
> # in a Python 3.8–3.13 environment
> pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
> pip install -e '.[ocr-paddle]'
> ```

---

## Region detectors (`mfo detect --detector …`)

| Name | What it does | Install |
|------|--------------|---------|
| `baseline` *(default)* | OpenCV connected-components; no model download | built in |
| `paddle` | PaddleOCR's text-detection model (tight text boxes) | `pip install 'mfo[ocr-paddle]'` |
| `paddle-rec` | PaddleOCR **detect + recognize** in one pass; `mfo ocr` reuses the text | `pip install 'mfo[ocr-paddle]'` |
| `ml` | a trained bubble/text detector (ONNX) | `pip install 'mfo[detect]'` + a model |

```bash
mfo detect <proj>                       # baseline (offline, zero setup)
mfo detect <proj> --detector paddle     # text-box detector; falls back to baseline if absent
mfo detect <proj> --detector paddle-rec # detect + recognize; OCR reuses the text (see below)
mfo detect <proj> --detector ml         # trained detector; falls back to baseline if absent
```

`paddle`, `paddle-rec` and `ml` all transparently fall back to the baseline if their dependency or
model isn't available, so detection never hard-fails.

### `paddle-rec` — recognize during detection (skip a second OCR pass)

PaddleOCR detects *and* recognizes text in one model pass, so running its OCR again per region just
repeats work. The `paddle-rec` detector captures the recognized text (with real per-box confidence)
while detecting; `mfo ocr` then **reuses** it instead of re-running OCR:

```bash
pip install 'mfo[ocr-paddle]'
mfo detect <proj> --detector paddle-rec   # boxes + text in one pass
mfo ocr <proj>                            # reuses the detection text — no second paddle pass
mfo translate <proj>
```

`mfo ocr` reuses detection text by default and only OCRs regions that lack it. To recognize
everything with a specific engine instead — making `--engine` authoritative — pass
`--no-reuse-detection` (or `--force`):

```bash
mfo ocr <proj> --engine manga-ocr --no-reuse-detection   # ignore detection text; use manga-ocr
```

Regions you redraw or split in `mfo review` no longer match the detected text, so they're re-OCR'd
on demand there as usual. Detection-provided text is recorded with its provenance, so it's fully
traceable and editable like any OCR.

**Baseline note:** the baseline can't tell a speech bubble from a panel, so blobs that are oversized
or span most of the page width are auto-marked **ignore** (kept in the data, but skipped by OCR,
rendering, and the review queue). `paddle`/`ml` box actual text and avoid this. You can always fix
detection by hand in `mfo review` (draw, move, merge, split, delete regions).

**Overlap merging (on by default).** Detectors often split one speech bubble into several
overlapping line-boxes. By default mfo merges regions whose boxes overlap into one region per
bubble, so OCR reads the whole bubble at once and the translation renders in a single box. Tune how
eagerly it merges with `--overlap-frac` (the fraction of the *smaller* box that must overlap; lower
= more merging), or turn it off entirely:

```bash
mfo detect <proj> --overlap-frac 0.1        # merge more aggressively
mfo detect <proj> --no-merge-overlap        # keep every detected box separate
```

For a det+rec detector (`paddle-rec`), a merged bubble's per-line text is dropped so `mfo ocr`
re-reads the whole merged crop (correct multi-line order); un-merged boxes keep their text.

### Using the `ml` detector

The ML detector needs an ONNX export of a comic/text detector. Cache it where mfo looks, or point
it at a URL to download once:

```bash
pip install 'mfo[detect]'
export MFO_MODEL_DIR="$HOME/.cache/mfo/models"     # optional; this is the default
# place comic-text-detector.onnx in $MFO_MODEL_DIR (or configure MLDetectorConfig.model_url)
mfo detect <proj> --detector ml
```

GPU is opt-in (configure the ONNX execution providers); CPU works out of the box.
