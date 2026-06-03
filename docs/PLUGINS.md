# Writing mfo plugins (third-party adapters)

mfo's pipeline stages are built on **adapters over providers** (NFR-17): detection, OCR,
translation, AI assist, and rendering are all pluggable interfaces with at least one offline
built-in. As of Batch 8.3 you can ship an extra adapter from your **own package** — no fork, no
edit to mfo — by declaring a Python **entry point**. The `get_*` resolvers consult mfo's built-ins
first, then entry points, so:

- the offline built-ins always resolve and can never be shadowed by an installed plugin (I-7/I-8);
- a broken plugin is skipped with a warning, never fatally (NFR-9) — one bad package can't break
  the core path.

## Entry-point groups

Each adapter layer has its own group. Register your factory under the matching group name:

| Layer | Group | Resolver | Factory signature |
|-------|-------|----------|-------------------|
| Detection | `mfo.detectors` | `mfo.vision.detect.get_detector` | `(*, lang: str \| None) -> RegionDetector` |
| OCR | `mfo.ocr` | `mfo.vision.ocr.get_ocr_engine` | `(*, lang: str \| None) -> OCREngine` |
| Translation | `mfo.translators` | `mfo.language.translate.get_translator` | `() -> Translator` |
| AI assist | `mfo.assistants` | `mfo.language.assist.get_assistant` | `() -> AiAssistant` |
| Rendering | `mfo.renderers` | *(reserved)* | *(render is not yet adapter-pluggable; group reserved for a future batch)* |

The **entry-point name** is the string a user passes on the CLI (e.g. `mfo detect --detector
<name>`). The **entry-point value** points at a zero-argument **factory** (`module:function`) that
returns a fresh adapter instance — not the adapter class itself. `lang` (the project's source
language) is passed by keyword to detector/OCR factories; ignore it if your adapter doesn't need it.

## Example: a third-party detector

In your package's `pyproject.toml`:

```toml
[project.entry-points."mfo.detectors"]
acme = "acme_mfo.detector:make_detector"
```

In `acme_mfo/detector.py`:

```python
from mfo.vision.detect import DetectedRegion, RegionDetector


class AcmeDetector:
    name = "acme"
    version = "1"

    def detect(self, image):  # image: HxWx3 uint8 ndarray (source pixels)
        # ... return a list[DetectedRegion] with BBox coords in source pixels ...
        return []


def make_detector(*, lang=None):
    return AcmeDetector()
```

Implement the layer's `Protocol` (`RegionDetector`, `OCREngine`, `Translator`, `AiAssistant`) — see
[ARCHITECTURE.md](ARCHITECTURE.md) for each contract and `src/mfo/vision/detect.py` for a worked
example. Once your package is installed in the same environment as mfo:

```bash
mfo detect myproject --detector acme
```

mfo discovers the entry point, runs your detector, and the offline built-ins keep working with no
plugin installed.

## Guidance

- **Fail lazily.** If your adapter needs a heavy or optional dependency, import it inside the
  adapter (not at module import time) and raise the layer's `*DependencyError` with a `pip install`
  hint, mirroring the built-ins. The factory itself should stay import-light so discovery is cheap.
- **Stay offline-respecting.** Network-dependent adapters are fine, but they must be opt-in and
  never sit on the mandatory core path (I-7/I-8).
- **Don't shadow a built-in name.** Built-ins win; pick a distinct name so users can select yours.
- **A curated plugin index** ("marketplace", SG-9) is just a list of packages publishing these
  entry points — register yours and it can be listed.
