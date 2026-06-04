"""The ``mfo`` command-line application (spec FR-46, FR-47).

Commands are deliberately thin: they resolve configuration and a project, then delegate to the
core/storage/vision layers. ``init``, ``import``, ``preprocess``, ``detect``, ``order``, ``group``,
``ocr``, ``translate``, ``glossary`` (add/list/remove), ``flag``, ``render`` (mask/remove source
text), ``status``, ``export`` (composite translated pages + the source→OCR→translation JSON mapping,
manifest, and transcript; ``--mapping`` for the mapping alone), ``run`` (the pipeline
orchestrator), and ``review`` (launch the local web editor) are functional.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated

import typer

from mfo import __version__
from mfo.cli.config import build_settings
from mfo.cli.logging import configure_logging, get_logger
from mfo.cli.stages import (
    COMPOSITE_SIGNATURE,
    COMPOSITE_STAGE,
    DETECT_STAGE,
    OCR_STAGE,
    PREPROCESS_STAGE,
    RENDER_STAGE,
    TRANSLATE_STAGE,
    apply_series_preset,
    archive_extract_dir,
    build_pipeline,
    composite_page_file,
    link_series_glossary,
    load_effective_glossary,
    load_glossary,
    save_assist_config,
    save_detect_config,
    save_glossary,
    save_group_config,
    save_import_config,
    save_ocr_config,
    save_preprocess_config,
    save_render_config,
    save_sfx_config,
    save_structure_config,
    save_translate_config,
    series_glossary_path,
    sfx_skip_types,
)
from mfo.core import (
    DEFAULT_THRESHOLD,
    AssetError,
    AssetStatus,
    AssistMode,
    GlossaryEntry,
    InMemoryStateStore,
    OCRSpan,
    Page,
    Project,
    ReadingDirection,
    Region,
    RenderArtifact,
    RenderPreset,
    SeriesPreset,
    SfxMode,
    TranslationStyle,
    TranslationUnit,
    default_model_dir,
    find_asset,
    find_preset,
    iter_assets,
    merge_entries,
    pull_asset,
    remove_entry,
    remove_preset,
    resolve_jobs,
    series_preset_names,
    upsert_entry,
    upsert_preset,
)
from mfo.core.assets import asset_status
from mfo.core.grouping import DEFAULT_GAP_RATIO
from mfo.language import (
    AssistDependencyError,
    AssistRequest,
    TranslationRequest,
    TranslatorDependencyError,
    get_assistant,
    get_translator,
)
from mfo.language.ocr_correct import (
    DEFAULT_MAX_ALTERNATIVES,
    OcrCorrectionRequest,
    OcrCorrectorDependencyError,
    get_ocr_corrector,
)
from mfo.language.transliterate import get_transliterator
from mfo.render import MaskConfig, mask_file
from mfo.storage import (
    DEFAULT_MIN_CONFIDENCE,
    MASK_KIND,
    RENDER_KIND,
    JsonStateStore,
    ProjectStore,
    assign_reading_order,
    assist_units,
    composite_pages,
    confidence_report,
    correct_ocr_spans,
    detect_regions,
    export_pages,
    flag_low_confidence,
    group_into_units,
    import_pages,
    load_series_glossary,
    load_series_presets,
    mask_pages,
    ocr_regions,
    preprocess_pages,
    process_sfx,
    save_series_glossary,
    save_series_presets,
    translate_units,
    write_mapping,
)
from mfo.vision import (
    DEFAULT_OVERLAP_FRAC,
    OcrDependencyError,
    PageOrder,
    PreprocessConfig,
    SfxFeatures,
    classify_region_type,
    detect_file,
    detect_panels_file,
    discover_images,
    get_detector,
    get_ocr_engine,
    get_sfx_classifier,
    is_archive,
    preprocess_file,
    recognize_file,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="mfo — manga/manhua OCR & context-aware translation pipeline.",
)
log = get_logger("cli")

# Shared performance knob for the heavy per-page stages: how many pages to process concurrently. It
# never affects the cached result, only how fast it's produced (NFR-5/6/7); 0 = auto (CPU count).
JobsOption = Annotated[
    int,
    typer.Option(
        "--jobs",
        "-j",
        min=0,
        help="Process this many pages concurrently (0 = auto/CPU count). Does not change results.",
    ),
]


def _open_store(path: Path) -> ProjectStore:
    """Open a project or exit with a helpful message (NFR-12)."""
    try:
        return ProjectStore.open(path)
    except FileNotFoundError:
        typer.secho(f"No mfo project found at {path}.", fg=typer.colors.RED, err=True)
        typer.secho("Create one with:  mfo init <dir>", err=True)
        raise typer.Exit(code=1) from None


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mfo {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    log_level: Annotated[
        str, typer.Option("--log-level", help="Logging level (e.g. INFO, DEBUG).")
    ] = "INFO",
) -> None:
    """mfo command-line interface."""
    configure_logging(log_level)


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Directory to create the project in.")],
    name: Annotated[
        str | None, typer.Option(help="Project name (defaults to the directory name).")
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", "-s", help="Source language code.")
    ] = None,
    target: Annotated[
        str | None, typer.Option("--target", "-t", help="Target language code.")
    ] = None,
    direction: Annotated[ReadingDirection | None, typer.Option(help="Reading direction.")] = None,
    config: Annotated[Path | None, typer.Option("--config", "-c", help="TOML config file.")] = None,
) -> None:
    """Create a new mfo project."""
    settings = build_settings(
        config,
        name=name,
        source_lang=source,
        target_lang=target,
        reading_direction=direction,
    )
    project = Project(
        name=settings.name or path.resolve().name,
        source_lang=settings.source_lang,
        target_lang=settings.target_lang,
        reading_direction=settings.reading_direction,
    )
    try:
        ProjectStore.create(path, project).close()
    except FileExistsError:
        typer.secho(f"A project already exists at {path}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    typer.secho(f"Created project {project.name!r} ({project.id})", fg=typer.colors.GREEN)
    typer.echo(f"  path:      {path.resolve()}")
    typer.echo(f"  languages: {project.source_lang} -> {project.target_lang}")
    typer.echo(f"  direction: {project.reading_direction}")


def _read_manifest_order(path: Path) -> list[str]:
    """Read an ordering manifest: one filename per line; blanks and ``#`` comments ignored."""
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


@app.command(name="import")
def import_(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    source: Annotated[
        Path, typer.Argument(help="Folder of source page images, or a .cbz/.zip archive.")
    ],
    order: Annotated[PageOrder, typer.Option(help="Page ordering strategy.")] = PageOrder.NATURAL,
    manifest: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            help="Ordering manifest (one filename per line); implies --order manifest.",
        ),
    ] = None,
) -> None:
    """Import page images from a folder or CBZ/ZIP archive (originals never modified)."""
    manifest_order = _read_manifest_order(manifest) if manifest is not None else None
    if manifest_order is not None:
        order = PageOrder.MANIFEST

    with _open_store(path) as store:
        if not (source.is_dir() or (source.is_file() and is_archive(source))):
            typer.secho(
                f"Not a directory or supported archive (.cbz/.zip): {source}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from None
        # Record the source first so an interrupted import can still be resumed by `mfo run`.
        save_import_config(store, source=source, order=order, manifest_order=manifest_order)
        extract_to = archive_extract_dir(store, source) if is_archive(source) else None
        scan = discover_images(
            source, order=order, manifest_order=manifest_order, extract_to=extract_to
        )
        pages = import_pages(store, scan.images)

    for skip in scan.skipped:
        typer.secho(f"  skipped {skip.source_path.name}: {skip.reason}", fg=typer.colors.YELLOW)
    typer.secho(f"Imported {len(pages)} page(s).", fg=typer.colors.GREEN)
    if scan.skipped:
        typer.echo(f"  {len(scan.skipped)} file(s) skipped.")


@app.command()
def preprocess(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    grayscale: Annotated[
        bool, typer.Option("--grayscale", help="Normalize to grayscale instead of RGB.")
    ] = False,
    max_dimension: Annotated[
        int | None,
        typer.Option("--max-dim", help="Downscale analysis derivative to this longest edge."),
    ] = None,
    denoise: Annotated[bool, typer.Option("--denoise", help="Apply a denoising filter.")] = False,
    deskew: Annotated[bool, typer.Option("--deskew", help="Estimate and correct page skew.")] = (
        False
    ),
    force: Annotated[
        bool, typer.Option("--force", help="Recompute even if a current derivative is cached.")
    ] = False,
) -> None:
    """Build normalized analysis derivatives for imported pages (originals untouched)."""
    config = PreprocessConfig(
        grayscale=grayscale, max_dimension=max_dimension, denoise=denoise, deskew=deskew
    )
    with _open_store(path) as store:
        save_preprocess_config(store, config)
        pages = preprocess_pages(
            store,
            transform=lambda image_path: preprocess_file(image_path, config),
            signature=config.signature(),
            force=force,
        )
    typer.secho(f"Preprocessed {len(pages)} page(s).", fg=typer.colors.GREEN)


@app.command()
def detect(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    detector: Annotated[
        str,
        typer.Option(
            "--detector", help="Region detector: 'baseline', 'ml', 'paddle', or 'paddle-rec'."
        ),
    ] = "baseline",
    merge_overlap: Annotated[
        bool,
        typer.Option(
            "--merge-overlap/--no-merge-overlap",
            help="Merge overlapping detected boxes into one region per bubble (on by default).",
        ),
    ] = True,
    overlap_frac: Annotated[
        float,
        typer.Option(
            "--overlap-frac",
            min=0.0,
            max=1.0,
            help="Overlap (fraction of the smaller box) at which two regions merge; lower = more.",
        ),
    ] = DEFAULT_OVERLAP_FRAC,
    force: Annotated[
        bool, typer.Option("--force", help="Re-detect even if a current result is cached.")
    ] = False,
    jobs: JobsOption = 1,
) -> None:
    """Detect text regions on imported pages.

    The default 'baseline' detector is offline and needs no model download; 'ml' uses a trained
    detector when available (pip install 'mfo[detect]') and 'paddle' uses PaddleOCR's text detector
    (pip install 'mfo[ocr-paddle]'). 'paddle-rec' runs PaddleOCR's full detect+recognize pipeline so
    `mfo ocr` can reuse its text without a second pass. All transparently fall back to the baseline
    if their dependency or model is absent. Overlapping boxes (e.g. a bubble split into several
    lines) are merged into one region by default; tune with --overlap-frac or turn off with
    --no-merge-overlap.
    """
    with _open_store(path) as store:
        try:
            engine = get_detector(
                detector,
                lang=store.project.source_lang,
                merge_overlap=merge_overlap,
                overlap_frac=overlap_frac,
            )
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        signature = f"{engine.name}@{engine.version}"
        save_detect_config(store, detector, merge_overlap=merge_overlap, overlap_frac=overlap_frac)
        regions = detect_regions(
            store,
            detect=lambda image_path: detect_file(image_path, engine),
            signature=signature,
            force=force,
            jobs=resolve_jobs(jobs),
        )
        total = len(store.db.list(Region))
    new = len(regions)
    if new == total:
        typer.secho(f"Detected {new} region(s).", fg=typer.colors.GREEN)
    else:
        # Pages whose source + detector are unchanged are skipped (NFR-8), so `new` can be 0 even
        # though the project already holds regions. Report both so a cached run isn't mistaken for
        # a failed one.
        typer.secho(
            f"Detected {new} new region(s); {total} total in project "
            f"({total - new} reused from cache — pass --force to re-detect).",
            fg=typer.colors.GREEN,
        )


@app.command()
def ocr(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    engine: Annotated[str, typer.Option("--engine", help="OCR engine to use.")] = "manga-ocr",
    reuse_detection: Annotated[
        bool,
        typer.Option(
            "--reuse-detection/--no-reuse-detection",
            help="Reuse text recognized during detection (e.g. by 'paddle-rec') instead of "
            "re-running OCR; --no-reuse-detection recognizes everything with --engine.",
        ),
    ] = True,
    force: Annotated[
        bool, typer.Option("--force", help="Re-run OCR even if a current result is cached.")
    ] = False,
    jobs: JobsOption = 1,
) -> None:
    """Recognize text on detected regions (default engine manga-ocr — install with mfo[ocr]).

    If detection used a det+rec detector (`mfo detect --detector paddle-rec`), the recognized text
    is reused by default and only regions without it are OCR'd; pass --no-reuse-detection to
    recognize everything with --engine.
    """
    with _open_store(path) as store:
        try:
            ocr_engine = get_ocr_engine(engine, lang=store.project.source_lang)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        signature = f"{ocr_engine.name}@{ocr_engine.version}"
        save_ocr_config(store, engine)
        try:
            spans = ocr_regions(
                store,
                recognize=lambda image_path, bbox: recognize_file(image_path, bbox, ocr_engine),
                signature=signature,
                reuse_detection=reuse_detection,
                force=force,
                jobs=resolve_jobs(jobs),
            )
        except OcrDependencyError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        reused = sum(int(page.ocr.get("reused", 0)) for page in store.db.list(Page))
    if reused:
        typer.secho(
            f"Recognized {len(spans)} region(s); {reused} reused from detection.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(f"Recognized {len(spans)} region(s).", fg=typer.colors.GREEN)


@app.command()
def translate(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    translator: Annotated[
        str,
        typer.Option(
            "--translator",
            help="Translator: 'argos' (offline default), 'api' (OpenAI-compatible; MFO_API_* env), "
            "or 'deepl' (MFO_DEEPL_API_KEY).",
        ),
    ] = "argos",
    style: Annotated[
        TranslationStyle, typer.Option("--style", help="Translation register (FR-25).")
    ] = TranslationStyle.BALANCED,
    force: Annotated[
        bool, typer.Option("--force", help="Re-translate even if a current result is cached.")
    ] = False,
    jobs: JobsOption = 1,
) -> None:
    """Translate units with context/glossary/style, offline (Argos default — mfo[translate])."""
    try:
        engine = get_translator(translator)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    signature = f"{engine.name}@{engine.version}"
    with _open_store(path) as store:
        save_translate_config(store, translator, style=style)
        source_lang = store.project.source_lang
        target_lang = store.project.target_lang
        glossary = load_effective_glossary(store)
        try:
            units = translate_units(
                store,
                translate=lambda source, context: engine.translate(
                    TranslationRequest(
                        source=source,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        context=context,
                        style=style,
                    )
                ),
                signature=signature,
                target_lang=target_lang,
                style=style,
                glossary=glossary,
                force=force,
                jobs=resolve_jobs(jobs),
            )
        except TranslatorDependencyError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
    typer.secho(f"Translated {len(units)} unit(s) ({style.value}).", fg=typer.colors.GREEN)


@app.command()
def assist(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[
        AssistMode,
        typer.Option(
            "--mode",
            help="AI mode (§12.4): 'assist' (suggest only), 'review' (highlight the AI candidate), "
            "or 'auto' (apply high-confidence suggestions). Never overwrites approved text.",
        ),
    ] = AssistMode.ASSIST,
    assistant: Annotated[
        str, typer.Option("--assistant", help="AI assistant adapter (MFO_AI_* / MFO_API_* env).")
    ] = "llm",
    min_confidence: Annotated[
        float,
        typer.Option(
            "--min-confidence",
            min=0.0,
            max=1.0,
            help="Confidence an 'auto' suggestion must reach before it is applied.",
        ),
    ] = DEFAULT_MIN_CONFIDENCE,
    style: Annotated[
        TranslationStyle, typer.Option("--style", help="Translation register (FR-25).")
    ] = TranslationStyle.BALANCED,
    force: Annotated[
        bool, typer.Option("--force", help="Re-run even if a current result is cached.")
    ] = False,
) -> None:
    """Refine translations with the optional AI layer (opt-in; configured from MFO_AI_*/MFO_API_*).

    Disabled by default and not part of ``mfo run`` — the offline core is unaffected (I-7).
    """
    try:
        engine = get_assistant(assistant)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    signature = f"{engine.name}@{engine.version}"
    with _open_store(path) as store:
        save_assist_config(store, assistant, mode=mode, min_confidence=min_confidence, style=style)
        source_lang = store.project.source_lang
        target_lang = store.project.target_lang
        try:
            units = assist_units(
                store,
                suggest=lambda source, draft, context: engine.suggest(
                    AssistRequest(
                        source=source,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        draft=draft,
                        context=context,
                        style=style,
                    )
                ),
                signature=signature,
                mode=mode,
                min_confidence=min_confidence,
                force=force,
            )
        except AssistDependencyError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
    typer.secho(f"AI {mode.value}: processed {len(units)} unit(s).", fg=typer.colors.GREEN)


@app.command(name="ocr-correct")
def ocr_correct(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    corrector: Annotated[
        str, typer.Option("--corrector", help="OCR corrector adapter (MFO_AI_* / MFO_API_* env).")
    ] = "llm",
    threshold: Annotated[
        float,
        typer.Option("--threshold", help="Only correct spans whose confidence is below this."),
    ] = DEFAULT_THRESHOLD,
    max_alternatives: Annotated[
        int,
        typer.Option("--max-alternatives", min=1, help="Corrected readings to propose per span."),
    ] = DEFAULT_MAX_ALTERNATIVES,
    force: Annotated[
        bool, typer.Option("--force", help="Re-run even if a current result is cached.")
    ] = False,
) -> None:
    """Suggest LLM corrections for low-confidence OCR as alternatives (opt-in; off the core path).

    Never overwrites recognized text — it only proposes alternate readings for review (I-3). Off by
    default and not part of ``mfo run``; the offline pipeline is unaffected (I-7, SG-7).
    """
    try:
        engine = get_ocr_corrector(corrector)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    signature = f"{engine.name}@{engine.version}"
    with _open_store(path) as store:
        source_lang = store.project.source_lang
        try:
            spans = correct_ocr_spans(
                store,
                correct=lambda text: (
                    engine.correct(
                        OcrCorrectionRequest(
                            text=text, source_lang=source_lang, max_alternatives=max_alternatives
                        )
                    ).alternatives
                ),
                signature=signature,
                threshold=threshold,
                force=force,
            )
        except OcrCorrectorDependencyError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
    typer.secho(
        f"OCR correction: suggested alternatives for {len(spans)} span(s).", fg=typer.colors.GREEN
    )


glossary_app = typer.Typer(
    no_args_is_help=True,
    help="Manage the project glossary (pinned source→target terms; FR-23, FR-24).",
)
app.add_typer(glossary_app, name="glossary")


@glossary_app.command("add")
def glossary_add(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    source: Annotated[str, typer.Argument(help="Source-language term to pin.")],
    target: Annotated[str, typer.Argument(help="Canonical target rendering.")],
    alias: Annotated[
        list[str] | None,
        typer.Option("--alias", help="Variant target spelling to normalize (repeatable)."),
    ] = None,
    note: Annotated[str | None, typer.Option("--note", help="Optional human note.")] = None,
) -> None:
    """Add or replace a glossary entry (matched by source term)."""
    entry = GlossaryEntry(source=source, target=target, aliases=tuple(alias or ()), notes=note)
    with _open_store(path) as store:
        existing = [e for e in load_glossary(store) if e.source != source]
        save_glossary(store, (*existing, entry))
    typer.secho(f"Glossary: {source!r} -> {target!r}", fg=typer.colors.GREEN)


@glossary_app.command("list")
def glossary_list(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
) -> None:
    """List the project glossary entries."""
    with _open_store(path) as store:
        entries = load_glossary(store)
    if not entries:
        typer.echo("No glossary entries.")
        return
    for entry in entries:
        line = f"  {entry.source} -> {entry.target}"
        if entry.aliases:
            line += f"  (aliases: {', '.join(entry.aliases)})"
        if entry.notes:
            line += f"  # {entry.notes}"
        typer.echo(line)


@glossary_app.command("remove")
def glossary_remove(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    source: Annotated[str, typer.Argument(help="Source term of the entry to remove.")],
) -> None:
    """Remove a glossary entry by its source term."""
    with _open_store(path) as store:
        entries = load_glossary(store)
        remaining = tuple(e for e in entries if e.source != source)
        if len(remaining) == len(entries):
            typer.secho(f"No glossary entry for {source!r}.", fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1) from None
        save_glossary(store, remaining)
    typer.secho(f"Removed {source!r}.", fg=typer.colors.GREEN)


@glossary_app.command("promote")
def glossary_promote(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    source: Annotated[str, typer.Argument(help="Source term of the project entry to promote.")],
) -> None:
    """Promote a project glossary entry into the linked series store, shared across volumes."""
    with _open_store(path) as store:
        store_path = series_glossary_path(store)
        if store_path is None:
            typer.secho(
                "No series glossary linked; run 'mfo glossary series link' first.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from None
        entry = next((e for e in load_glossary(store) if e.source == source), None)
        if entry is None:
            typer.secho(f"No project glossary entry for {source!r}.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        series = upsert_entry(load_series_glossary(store_path), entry)
        save_series_glossary(store_path, series)
    typer.secho(
        f"Promoted {source!r} -> {entry.target!r} to the series glossary.", fg=typer.colors.GREEN
    )


series_app = typer.Typer(
    no_args_is_help=True,
    help="Manage the shared series glossary (terminology memory across volumes; SG-2, SG-3).",
)
glossary_app.add_typer(series_app, name="series")


@series_app.command("link")
def series_link(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    store_path: Annotated[Path, typer.Argument(help="Shared series-glossary file (JSON).")],
) -> None:
    """Link this project to a shared series-glossary store so its volumes inherit terms (SG-2)."""
    with _open_store(path) as store:
        link_series_glossary(store, store_path)
        if not store_path.exists():
            save_series_glossary(store_path, load_series_glossary(store_path))
    typer.secho(f"Linked series glossary: {store_path}", fg=typer.colors.GREEN)


@series_app.command("list")
def series_list(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
) -> None:
    """List the linked series-glossary entries."""
    with _open_store(path) as store:
        store_path = series_glossary_path(store)
        if store_path is None:
            typer.echo("No series glossary linked.")
            return
        series = load_series_glossary(store_path)
    if not series.entries:
        typer.echo("No series glossary entries.")
        return
    for entry in series.entries:
        line = f"  {entry.source} -> {entry.target}"
        if entry.aliases:
            line += f"  (aliases: {', '.join(entry.aliases)})"
        if entry.notes:
            line += f"  # {entry.notes}"
        typer.echo(line)


@series_app.command("remove")
def series_remove(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    source: Annotated[str, typer.Argument(help="Source term of the series entry to remove.")],
) -> None:
    """Remove an entry from the linked series glossary by its source term."""
    with _open_store(path) as store:
        store_path = series_glossary_path(store)
        if store_path is None:
            typer.secho("No series glossary linked.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        series = load_series_glossary(store_path)
        if all(e.source != source for e in series.entries):
            typer.secho(
                f"No series glossary entry for {source!r}.", fg=typer.colors.YELLOW, err=True
            )
            raise typer.Exit(code=1) from None
        save_series_glossary(store_path, remove_entry(series, source))
    typer.secho(f"Removed {source!r} from the series glossary.", fg=typer.colors.GREEN)


@series_app.command("export")
def series_export(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    out: Annotated[Path, typer.Argument(help="Destination file for the shared glossary (JSON).")],
) -> None:
    """Export the linked series glossary to a file for team sharing (lossless; SG-2)."""
    with _open_store(path) as store:
        store_path = series_glossary_path(store)
        if store_path is None:
            typer.secho("No series glossary linked.", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        save_series_glossary(out, load_series_glossary(store_path))
    typer.secho(f"Exported series glossary -> {out}", fg=typer.colors.GREEN)


@series_app.command("import")
def series_import(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    src: Annotated[Path, typer.Argument(help="Series-glossary file to import (JSON).")],
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Replace the linked store instead of merging into it."),
    ] = False,
) -> None:
    """Import a shared series glossary into the linked store (merge by default; SG-2)."""
    with _open_store(path) as store:
        store_path = series_glossary_path(store)
        if store_path is None:
            typer.secho(
                "No series glossary linked; run 'mfo glossary series link' first.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from None
        incoming = load_series_glossary(src)
        if replace:
            merged = incoming
        else:
            merged = merge_entries(load_series_glossary(store_path), incoming.entries)
        save_series_glossary(store_path, merged)
    verb = "Replaced with" if replace else "Merged in"
    typer.secho(f"{verb} {len(incoming.entries)} series entr(ies).", fg=typer.colors.GREEN)


preset_app = typer.Typer(
    no_args_is_help=True,
    help="Manage per-series style presets (style + shared glossary + render config; SG-4).",
)
app.add_typer(preset_app, name="preset")


@preset_app.command("save")
def preset_save(
    store_path: Annotated[Path, typer.Argument(help="Series-preset store file (JSON).")],
    name: Annotated[str, typer.Argument(help="Preset name (unique within the store).")],
    style: Annotated[
        TranslationStyle, typer.Option("--style", help="Translation register (FR-25).")
    ] = TranslationStyle.BALANCED,
    glossary: Annotated[
        Path | None,
        typer.Option("--glossary", help="Shared series-glossary store to link when applied (8.5)."),
    ] = None,
    pad: Annotated[
        int, typer.Option("--pad", help="Render: grow each masked box by this many px.")
    ] = 2,
    border: Annotated[
        int, typer.Option("--border", help="Render: width (px) of the background-sampling ring.")
    ] = 4,
) -> None:
    """Define or replace a named series preset (matched by name)."""
    preset = SeriesPreset(
        name=name,
        style=style,
        glossary_path=str(glossary) if glossary is not None else None,
        render=RenderPreset(pad=pad, border=border),
    )
    store = upsert_preset(load_series_presets(store_path), preset)
    save_series_presets(store_path, store)
    typer.secho(f"Saved preset {name!r} ({style.value}).", fg=typer.colors.GREEN)


@preset_app.command("list")
def preset_list(
    store_path: Annotated[Path, typer.Argument(help="Series-preset store file (JSON).")],
) -> None:
    """List the presets in a series-preset store by name."""
    store = load_series_presets(store_path)
    if not store.presets:
        typer.echo("No presets.")
        return
    for preset in store.presets:
        line = f"  {preset.name}  ({preset.style.value}; pad={preset.render.pad}, "
        line += f"border={preset.render.border})"
        if preset.glossary_path:
            line += f"  glossary: {preset.glossary_path}"
        typer.echo(line)


@preset_app.command("remove")
def preset_remove(
    store_path: Annotated[Path, typer.Argument(help="Series-preset store file (JSON).")],
    name: Annotated[str, typer.Argument(help="Name of the preset to remove.")],
) -> None:
    """Remove a preset from a series-preset store by name."""
    store = load_series_presets(store_path)
    if name not in series_preset_names(store):
        typer.secho(f"No preset named {name!r}.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1) from None
    save_series_presets(store_path, remove_preset(store, name))
    typer.secho(f"Removed preset {name!r}.", fg=typer.colors.GREEN)


@preset_app.command("apply")
def preset_apply(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    store_path: Annotated[Path, typer.Argument(help="Series-preset store file (JSON).")],
    name: Annotated[str, typer.Argument(help="Name of the preset to apply.")],
) -> None:
    """Apply a series preset to a project: set style, link glossary, set render config (SG-4)."""
    preset = find_preset(load_series_presets(store_path), name)
    if preset is None:
        typer.secho(f"No preset named {name!r}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    with _open_store(path) as store:
        apply_series_preset(store, preset)
    typer.secho(f"Applied preset {name!r} ({preset.style.value}).", fg=typer.colors.GREEN)


@app.command()
def order(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    direction: Annotated[
        ReadingDirection | None,
        typer.Option(help="Reading direction (defaults to the project's)."),
    ] = None,
    panels: Annotated[
        bool,
        typer.Option(
            "--panels/--no-panels",
            help="Refine order with best-effort panel detection (reads page images — FR-18).",
        ),
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Recompute even if a current order is cached.")
    ] = False,
) -> None:
    """Infer reading order for detected regions (offline, column-aware; RTL/LTR — FR-16/17/18)."""
    with _open_store(path) as store:
        resolved = direction or store.project.reading_direction
        save_structure_config(store, resolved, panels=panels)
        regions = assign_reading_order(
            store,
            direction=resolved,
            detect_panels=detect_panels_file if panels else None,
            force=force,
        )
    typer.secho(f"Ordered {len(regions)} region(s) ({resolved}).", fg=typer.colors.GREEN)


@app.command()
def group(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    max_gap_ratio: Annotated[
        float,
        typer.Option(
            "--max-gap",
            help="Chain same-type regions within this fraction of their mean height into one unit. "
            f"0 (default) = one unit per bubble; try {DEFAULT_GAP_RATIO} to merge stacked bubbles.",
        ),
    ] = 0.0,
    force: Annotated[
        bool, typer.Option("--force", help="Recompute even if current grouping is cached.")
    ] = False,
) -> None:
    """Group ordered regions into units (offline; one unit per bubble by default — FR-19)."""
    with _open_store(path) as store:
        save_group_config(store, max_gap_ratio)
        units = group_into_units(store, max_gap_ratio=max_gap_ratio, force=force)
    typer.secho(f"Grouped regions into {len(units)} unit(s).", fg=typer.colors.GREEN)


@app.command()
def sfx(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    mode: Annotated[
        SfxMode,
        typer.Option(
            "--mode",
            help="How to handle SFX at render: 'render' (translate like dialogue — default), "
            "'transliterate' (typeset a romanization), or 'skip' (leave original SFX art alone).",
        ),
    ] = SfxMode.RENDER,
    classifier: Annotated[
        str, typer.Option("--classifier", help="SFX classifier adapter (offline 'heuristic').")
    ] = "heuristic",
    transliterator: Annotated[
        str, typer.Option("--transliterator", help="Transliterator adapter (offline 'kana').")
    ] = "kana",
) -> None:
    """Classify SFX regions and attach transliterations; set how SFX renders (offline; SG-5)."""
    try:
        classify = get_sfx_classifier(classifier)
        romanize = get_transliterator(transliterator)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    with _open_store(path) as store:
        save_sfx_config(store, mode=mode, classifier=classifier, transliterator=transliterator)
        source_lang = store.project.source_lang
        result = process_sfx(
            store,
            classify=lambda region, page: classify_region_type(
                SfxFeatures(
                    bbox=region.bbox,
                    region_type=region.type,
                    page_width=page.width,
                    page_height=page.height,
                ),
                classify,
            ),
            transliterate=lambda text: romanize.transliterate(text, source_lang=source_lang),
            mode=mode,
        )
    typer.secho(
        f"SFX ({mode.value}): classified {len(result.classified)} region(s), "
        f"transliterated {len(result.transliterated)} unit(s).",
        fg=typer.colors.GREEN,
    )


def _stage_line(label: str, count: int, unit: str) -> str:
    mark = "✓" if count else "·"
    state = f"{count} {unit}" if count else "pending"
    return f"  [{mark}] {label:<10} {state}"


@app.command()
def status(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    threshold: Annotated[
        float, typer.Option("--threshold", help="Confidence below which a region is low.")
    ] = DEFAULT_THRESHOLD,
) -> None:
    """Show project info, per-stage progress, and where confidence is low (I-4)."""
    with _open_store(path) as store:
        project = store.project
        pages = store.db.list(Page)
        preprocessed = sum(1 for page in pages if page.preprocessing.get("cache_key"))
        regions = store.db.list(Region)
        ordered = sum(1 for region in regions if region.reading_order_index is not None)
        ocr_spans = store.db.list(OCRSpan)
        units = store.db.list(TranslationUnit)
        translated = sum(1 for unit in units if unit.selected_candidate_id is not None)
        renders = store.db.list(RenderArtifact)
        masked = sum(1 for a in renders if a.params.get("kind") == MASK_KIND)
        composited = sum(1 for a in renders if a.params.get("kind") == RENDER_KIND)
        report = confidence_report(store, threshold=threshold)

    typer.secho(f"{project.name}  ({project.id})", bold=True)
    typer.echo(f"  languages: {project.source_lang} -> {project.target_lang}")
    typer.echo(f"  direction: {project.reading_direction}")
    typer.echo("")
    typer.echo("Stages:")
    typer.echo(_stage_line("import", len(pages), "pages"))
    typer.echo(_stage_line("preprocess", preprocessed, "pages"))
    typer.echo(_stage_line("detect", len(regions), "regions"))
    typer.echo(_stage_line("order", ordered, "regions"))
    typer.echo(_stage_line("group", len(units), "units"))
    typer.echo(_stage_line("ocr", len(ocr_spans), "spans"))
    typer.echo(_stage_line("translate", translated, "units"))
    typer.echo(_stage_line("render", masked, "pages"))
    typer.echo(_stage_line("compose", composited, "pages"))

    if report.total:
        typer.echo("")
        typer.echo("Confidence:")
        color = typer.colors.YELLOW if report.low else typer.colors.GREEN
        typer.secho(
            f"  low-confidence: {report.low}/{report.total} region(s) "
            f"(threshold {report.threshold:.2f})",
            fg=color,
        )
        typer.echo(f"  flagged for review: {report.flagged}")


@app.command()
def flag(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    threshold: Annotated[
        float, typer.Option("--threshold", help="Confidence below which a region is flagged.")
    ] = DEFAULT_THRESHOLD,
) -> None:
    """Mark low-confidence regions for review (only automatic ones — user edits win, I-3)."""
    with _open_store(path) as store:
        flagged = flag_low_confidence(store, threshold=threshold)
    typer.secho(f"Flagged {len(flagged)} region(s) for review.", fg=typer.colors.GREEN)


@app.command()
def render(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    pad: Annotated[
        int, typer.Option("--pad", help="Grow each region by this many px to catch text edges.")
    ] = 2,
    border: Annotated[
        int,
        typer.Option("--border", help="Width (px) of the ring sampled to estimate background."),
    ] = 4,
    force: Annotated[
        bool, typer.Option("--force", help="Re-mask even if a current result is cached.")
    ] = False,
    jobs: JobsOption = 1,
) -> None:
    """Mask/remove source text per page into a reversible masked layer (offline; FR-31/32/33)."""
    config = MaskConfig(pad=pad, border=border)
    with _open_store(path) as store:
        save_render_config(store, config)
        artifacts = mask_pages(
            store,
            mask=lambda image_path, boxes: mask_file(image_path, boxes, config),
            signature=config.signature(),
            skip_types=sfx_skip_types(store),
            force=force,
            jobs=resolve_jobs(jobs),
        )
    typer.secho(f"Masked {len(artifacts)} page(s).", fg=typer.colors.GREEN)


@app.command()
def run(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    stage: Annotated[
        str | None, typer.Option("--stage", help="Run only this pipeline stage.")
    ] = None,
    from_: Annotated[
        str | None, typer.Option("--from", help="Run from this stage and everything downstream.")
    ] = None,
    to: Annotated[
        str | None, typer.Option("--to", help="Run up to and including this stage.")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Re-run stages even if their inputs are unchanged.")
    ] = False,
    jobs: JobsOption = 1,
) -> None:
    """Run the processing pipeline."""
    with _open_store(path) as store:
        pipeline = build_pipeline(store, jobs=resolve_jobs(jobs))
        if not pipeline.stage_names():
            typer.secho(
                "Nothing to run yet — import a source first with:  mfo import <project> <source>",
                fg=typer.colors.YELLOW,
            )
            return
        state = JsonStateStore(store.layout.pipeline_state_path)
        try:
            results = pipeline.run(store, state, only=stage, from_=from_, to=to, force=force)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        for result in results:
            mark = "skip" if result.skipped else "run "
            typer.echo(f"  [{mark}] {result.name}")
        typer.secho("Pipeline complete.", fg=typer.colors.GREEN)


# The per-page heavy stages worth profiling; the rest (import/order/group) are bookkeeping.
_BENCH_STAGES = (
    PREPROCESS_STAGE,
    DETECT_STAGE,
    OCR_STAGE,
    TRANSLATE_STAGE,
    RENDER_STAGE,
    COMPOSITE_STAGE,
)


@app.command()
def bench(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    jobs: JobsOption = 1,
    stage: Annotated[str | None, typer.Option("--stage", help="Benchmark only this stage.")] = None,
) -> None:
    """Time the heavy pipeline stages at a given worker count (NFR-5/6/7).

    Each configured heavy stage is force re-run in dependency order and timed, so you can compare
    `--jobs 1` against, say, `--jobs 4` on a real volume. The timing run uses an in-memory state
    store, so it re-does the work without disturbing the project's own pipeline state (the stage
    outputs it recomputes are identical to a normal run — parallelism never changes the result).
    """
    with _open_store(path) as store:
        pipeline = build_pipeline(store, jobs=resolve_jobs(jobs))
        names = set(pipeline.stage_names())
        if stage is not None and stage not in names:
            typer.secho(
                f"unknown or unconfigured stage {stage!r}; known: {sorted(names)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        selected = [s for s in _BENCH_STAGES if s in names and (stage is None or s == stage)]
        if not selected:
            typer.secho(
                "Nothing to benchmark — configure the heavy stages first (detect/ocr/translate/…).",
                fg=typer.colors.YELLOW,
            )
            return

        # An in-memory state store so the timing re-runs don't rewrite the on-disk pipeline state.
        state = InMemoryStateStore()
        page_count = len(store.db.list(Page))
        typer.secho(
            f"Benchmarking {len(selected)} stage(s) over {page_count} page(s) "
            f"with --jobs {resolve_jobs(jobs)}:",
            fg=typer.colors.CYAN,
        )
        total = 0.0
        for name in selected:
            start = time.perf_counter()
            pipeline.run(store, state, only=name, force=True)
            elapsed = time.perf_counter() - start
            total += elapsed
            typer.echo(f"  {name:<11} {elapsed:8.3f}s")
        typer.secho(f"  {'total':<11} {total:8.3f}s", fg=typer.colors.GREEN)


@app.command()
def export(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output directory.")] = None,
    mapping: Annotated[
        bool,
        typer.Option(
            "--mapping", help="Export the source→OCR→translation mapping as JSON (FR-43)."
        ),
    ] = False,
    jobs: JobsOption = 1,
) -> None:
    """Export translated pages + the source→OCR→translation mapping (FR-43, MVP-9)."""
    with _open_store(path) as store:
        out_dir = out if out is not None else store.layout.exports_dir
        if mapping:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "mapping.json"
            write_mapping(store, out_path)
            typer.secho(f"Wrote mapping to {out_path}", fg=typer.colors.GREEN)
            return
        # Composite the selected translations onto the masked pages, then bundle the export.
        composite_pages(
            store,
            composite=composite_page_file,
            signature=COMPOSITE_SIGNATURE,
            skip_types=sfx_skip_types(store),
            jobs=resolve_jobs(jobs),
        )
        result = export_pages(store, out_dir)

    typer.secho(f"Exported {len(result.pages)} page(s) to {result.out_dir}", fg=typer.colors.GREEN)
    typer.echo(f"  mapping:    {result.mapping_path}")
    typer.echo(f"  manifest:   {result.manifest_path}")
    typer.echo(f"  transcript: {result.transcript_path}")
    if result.overflow:
        typer.secho(
            f"  {result.overflow} text placement(s) overflowed their box — review recommended.",
            fg=typer.colors.YELLOW,
        )


@app.command()
def sample(
    path: Annotated[
        Path, typer.Argument(help="Directory to write the synthetic sample page images into.")
    ],
    pages: Annotated[
        int, typer.Option("--pages", min=1, help="How many sample pages to generate.")
    ] = 2,
) -> None:
    """Generate a small synthetic sample dataset for an end-to-end trial run (§21).

    The pages are drawn locally (no download, no copyrighted art) and the offline baseline detector
    finds their text, so the printed sequence runs the whole pipeline on a clean machine.
    """
    from mfo.sample import create_sample_pages

    written = create_sample_pages(path, count=pages)
    typer.secho(f"Wrote {len(written)} sample page(s) to {path.resolve()}", fg=typer.colors.GREEN)
    typer.echo("Next steps (runs fully offline with the baseline detector):")
    typer.echo("  mfo init ./sample-project --source ja --target en")
    typer.echo(f"  mfo import ./sample-project {path}")
    typer.echo("  mfo run ./sample-project")
    typer.echo("  mfo export ./sample-project --out ./sample-out")


models_app = typer.Typer(
    no_args_is_help=True,
    help="Locate, inspect, and fetch the optional model assets (OCR/detector/translation).",
)
app.add_typer(models_app, name="models")


@models_app.command("path")
def models_path() -> None:
    """Show the directory optional model weights are cached in (set MFO_MODEL_DIR to change it)."""
    typer.echo(str(default_model_dir()))
    override = os.environ.get("MFO_MODEL_DIR")
    if override:
        typer.echo(f"  (from MFO_MODEL_DIR={override})")


@models_app.command("list")
def models_list() -> None:
    """List the known optional models and whether each is cached, missing, or library-managed."""
    typer.echo(f"{'NAME':<14} {'KIND':<9} {'STATUS':<8} SUMMARY")
    for asset in iter_assets():
        status = asset_status(asset)
        typer.echo(f"{asset.name:<14} {asset.kind:<9} {status.value:<8} {asset.summary}")


@models_app.command("pull")
def models_pull(
    name: Annotated[str, typer.Argument(help="Asset name (see `mfo models list`).")],
    url: Annotated[
        str | None,
        typer.Option("--url", help="Override the download URL for a downloadable asset."),
    ] = None,
) -> None:
    """Fetch a downloadable asset into the model cache; explain how to provision managed ones."""
    asset = find_asset(name)
    if asset is None:
        typer.secho(
            f"Unknown asset {name!r}. See `mfo models list`.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=1)
    try:
        result = pull_asset(asset, url=url)
    except AssetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if result.status is AssetStatus.MANAGED:
        typer.secho(
            f"{asset.name} is provisioned by its own library, not a direct download.",
            fg=typer.colors.YELLOW,
        )
        typer.echo(f"  {asset.install_hint}")
        return
    if result.downloaded:
        typer.secho(f"Downloaded {asset.name} to {result.path}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"{asset.name} already cached at {result.path}", fg=typer.colors.GREEN)


@app.command()
def review(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    host: Annotated[
        str,
        typer.Option(help="Host to bind to. Use 0.0.0.0 to share on the LAN for review (SG-8)."),
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to serve on.")] = 8000,
    token: Annotated[
        str | None,
        typer.Option(
            help="Require this shared token on every API request (recommended when not on "
            "localhost). Reviewers open the editor at /?token=<token>.",
        ),
    ] = None,
) -> None:
    """Launch the local web review editor (FR-36; §13).

    Bind to ``0.0.0.0`` to let co-reviewers on the same network open the editor (SG-8); pair it with
    ``--token`` to gate access. Concurrent edits are attributed per reviewer and a stale write is
    rejected with a conflict rather than silently overwriting another reviewer's work (SG-10, I-3).
    """
    try:
        from mfo.ui.server import serve
    except ModuleNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    # The server needs cross-thread access to the SQLite connection (uvicorn runs request
    # handlers off worker threads); the store stays read-mostly and edits go through the API.
    try:
        store = ProjectStore.open(path, check_same_thread=False)
    except FileNotFoundError:
        typer.secho(f"No mfo project found at {path}.", fg=typer.colors.RED, err=True)
        typer.secho("Create one with:  mfo init <dir>", err=True)
        raise typer.Exit(code=1) from None
    if host not in ("127.0.0.1", "localhost") and token is None:
        typer.secho(
            "Warning: serving on a non-local host without --token; anyone on the network can edit.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    try:
        url = f"http://{host}:{port}{f'/?token={token}' if token else ''}"
        typer.secho(f"mfo review serving {path} at {url}", fg=typer.colors.GREEN)
        typer.echo("Press Ctrl+C to stop.")
        serve(store, host=host, port=port, auth_token=token)
    finally:
        store.close()
