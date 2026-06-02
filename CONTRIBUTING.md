# Contributing to mfo

Thanks for your interest! mfo aims to be a **best-in-class, contributor-friendly** open-source
manga/manhua translation workstation (spec §18). This guide covers how to build, test, and
submit changes.

## Before you start

- Read [`PLAN.md`](PLAN.md) — work is organized into **milestones → batches**. Pick the next
  unchecked batch, or open an issue to propose something new.
- Skim [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md).
- The requirements in [`mfo_design_notes_spec.md`](mfo_design_notes_spec.md) are authoritative.

## Dev setup

> Tooling lands in batch 0.1; this is the intended setup.

```bash
git clone <repo> && cd mfo
python -m venv .venv && source .venv/bin/activate   # Python ≥ 3.11
pip install -e ".[dev]"
pre-commit install
```

## Quality gates (run before every PR)

```bash
ruff check . && ruff format --check .   # lint + format
mypy src                                # types
pytest                                  # tests
```

CI runs the same on Python 3.11–3.13; PRs must be green.

## How we work

1. **One batch per PR.** Keep changes scoped to a single batch from `PLAN.md`; keep `main`
   green.
2. **Tests with every batch** (NFR-29). Critical stages must be covered.
3. **Cite spec IDs** (`FR-*`, `NFR-*`, `I-*`, `MVP-*`) in commit messages and PR descriptions.
4. **When a batch lands:** tick it in `PLAN.md` and move its entry to `CHANGELOG.md` under a
   dated heading.

## Non-negotiable invariants

Any change must respect the spec invariants (§5) — see the list in [`CLAUDE.md`](CLAUDE.md).
In short: never destroy source images, keep full traceability, user edits win, keep confidence
visible, keep stages cacheable/restartable, and keep the core path offline.

## Code style

- **ruff** for lint + format; **mypy** for types. Match the surrounding code's idioms and
  comment density.
- Prefer explicit data structures over hidden state (spec §20).
- New OCR/detection/translation/render/AI providers are added as **adapters** behind the
  existing interfaces — never hard-code a vendor into a stage.
- Keep stage outputs serializable and stage boundaries clean.

## Commit messages

Conventional, imperative, and traceable:

```
feat(vision): add manga-ocr OCR adapter for Japanese

Implements per-region OCRSpan with confidence (FR-12, FR-13).
Satisfies MVP-4.
```

## Reporting issues

Include OS, Python version, a minimal sample (a page or two if possible), the exact command,
and the full error. For OCR/translation quality issues, attach the source region and the
produced output so it stays traceable.
