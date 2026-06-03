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

```bash
export MFO_API_KEY="sk-…"
export MFO_API_BASE_URL="https://api.openai.com/v1"     # or any compatible gateway
export MFO_API_MODEL="gpt-4o-mini"
mfo translate <proj> --translator api --style natural
```

Point `MFO_API_BASE_URL` at any service that speaks the OpenAI chat-completions format — including
LLM gateways and proxies that wrap **DeepL or Google Translate** behind an OpenAI-compatible API.

### What about "free Google Translate"?

There's no official *free* Google Translate API; the truly free path relies on **unofficial**
endpoints scraped from the consumer site, which violate Google's ToS and break without warning. mfo
deliberately does **not** bundle one. Your supported options for Google-quality output are:

- the official **Google Cloud Translation** API, reached through an OpenAI-compatible gateway via
  the `api` adapter above, or
- **DeepL**'s official free tier (`--translator deepl`).

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

> **PaddleOCR needs the `paddlepaddle` backend.** The `mfo[ocr-paddle]` extra targets PaddleOCR 3.x
> and pulls in `paddlepaddle`, which only ships wheels for some Python versions (currently 3.9–3.12).
> Installing `paddleocr` *without* a working `paddlepaddle` lets the engine import but not run; mfo
> reports this as a dependency error (OCR) or falls back to the baseline detector (detection) rather
> than crashing. If you hit this, install into a supported Python or `pip install paddlepaddle`.

---

## Region detectors (`mfo detect --detector …`)

| Name | What it does | Install |
|------|--------------|---------|
| `baseline` *(default)* | OpenCV connected-components; no model download | built in |
| `paddle` | PaddleOCR's text-detection model (tight text boxes) | `pip install 'mfo[ocr-paddle]'` |
| `ml` | a trained bubble/text detector (ONNX) | `pip install 'mfo[detect]'` + a model |

```bash
mfo detect <proj>                    # baseline (offline, zero setup)
mfo detect <proj> --detector paddle  # text-box detector; falls back to baseline if absent
mfo detect <proj> --detector ml      # trained detector; falls back to baseline if absent
```

Both `paddle` and `ml` transparently fall back to the baseline if their dependency or model isn't
available, so detection never hard-fails.

**Baseline note:** the baseline can't tell a speech bubble from a panel, so blobs that are oversized
or span most of the page width are auto-marked **ignore** (kept in the data, but skipped by OCR,
rendering, and the review queue). `paddle`/`ml` box actual text and avoid this. You can always fix
detection by hand in `mfo review` (draw, move, merge, split, delete regions).

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
