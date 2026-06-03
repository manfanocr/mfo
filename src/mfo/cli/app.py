"""The ``mfo`` command-line application (spec FR-46, FR-47).

Commands are deliberately thin: they resolve configuration and a project, then delegate to the
core/storage/vision layers. ``init``, ``import``, ``preprocess``, ``detect``, ``order``, ``group``,
``ocr``, ``translate``, ``glossary`` (add/list/remove), ``flag``, ``render`` (mask/remove source
text), ``status``, ``export`` (composite translated pages + the source→OCR→translation JSON mapping,
manifest, and transcript; ``--mapping`` for the mapping alone), ``run`` (the pipeline
orchestrator), and ``review`` (launch the local web editor) are functional.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from mfo import __version__
from mfo.cli.config import build_settings
from mfo.cli.logging import configure_logging, get_logger
from mfo.cli.stages import (
    COMPOSITE_SIGNATURE,
    build_pipeline,
    composite_page_file,
    load_glossary,
    save_detect_config,
    save_glossary,
    save_group_config,
    save_import_config,
    save_ocr_config,
    save_preprocess_config,
    save_render_config,
    save_structure_config,
    save_translate_config,
)
from mfo.core import (
    DEFAULT_THRESHOLD,
    GlossaryEntry,
    OCRSpan,
    Page,
    Project,
    ReadingDirection,
    Region,
    RenderArtifact,
    TranslationStyle,
    TranslationUnit,
)
from mfo.core.grouping import DEFAULT_GAP_RATIO
from mfo.language import (
    TranslationRequest,
    TranslatorDependencyError,
    get_translator,
)
from mfo.render import MaskConfig, mask_file
from mfo.storage import (
    MASK_KIND,
    RENDER_KIND,
    JsonStateStore,
    ProjectStore,
    assign_reading_order,
    composite_pages,
    confidence_report,
    detect_regions,
    export_pages,
    flag_low_confidence,
    group_into_units,
    import_pages,
    mask_pages,
    ocr_regions,
    preprocess_pages,
    translate_units,
    write_mapping,
)
from mfo.vision import (
    OcrDependencyError,
    PageOrder,
    PreprocessConfig,
    detect_file,
    detect_panels_file,
    discover_images,
    get_detector,
    get_ocr_engine,
    preprocess_file,
    recognize_file,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="mfo — manga/manhua OCR & context-aware translation pipeline.",
)
log = get_logger("cli")


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
    source: Annotated[Path, typer.Argument(help="Directory of source page images.")],
    order: Annotated[PageOrder, typer.Option(help="Page ordering strategy.")] = PageOrder.NATURAL,
    manifest: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            help="Ordering manifest (one filename per line); implies --order manifest.",
        ),
    ] = None,
) -> None:
    """Import a folder of page images into the project (originals are copied, never modified)."""
    manifest_order = _read_manifest_order(manifest) if manifest is not None else None
    if manifest_order is not None:
        order = PageOrder.MANIFEST

    with _open_store(path) as store:
        if not source.is_dir():
            typer.secho(f"Not a directory: {source}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
        # Record the source first so an interrupted import can still be resumed by `mfo run`.
        save_import_config(store, source=source, order=order, manifest_order=manifest_order)
        scan = discover_images(source, order=order, manifest_order=manifest_order)
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
        typer.Option("--detector", help="Region detector: 'baseline', 'ml', or 'paddle'."),
    ] = "baseline",
    force: Annotated[
        bool, typer.Option("--force", help="Re-detect even if a current result is cached.")
    ] = False,
) -> None:
    """Detect text regions on imported pages.

    The default 'baseline' detector is offline and needs no model download; 'ml' uses a trained
    detector when available (pip install 'mfo[detect]') and 'paddle' uses PaddleOCR's text detector
    (pip install 'mfo[ocr-paddle]'). Both transparently fall back to the baseline if their
    dependency or model is absent.
    """
    try:
        engine = get_detector(detector)
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    signature = f"{engine.name}@{engine.version}"
    with _open_store(path) as store:
        save_detect_config(store, detector)
        regions = detect_regions(
            store,
            detect=lambda image_path: detect_file(image_path, engine),
            signature=signature,
            force=force,
        )
    typer.secho(f"Detected {len(regions)} region(s).", fg=typer.colors.GREEN)


@app.command()
def ocr(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    engine: Annotated[str, typer.Option("--engine", help="OCR engine to use.")] = "manga-ocr",
    force: Annotated[
        bool, typer.Option("--force", help="Re-run OCR even if a current result is cached.")
    ] = False,
) -> None:
    """Recognize text on detected regions (default engine manga-ocr — install with mfo[ocr])."""
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
                force=force,
            )
        except OcrDependencyError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
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
        glossary = load_glossary(store)
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
            )
        except TranslatorDependencyError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
    typer.secho(f"Translated {len(units)} unit(s) ({style.value}).", fg=typer.colors.GREEN)


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
) -> None:
    """Mask/remove source text per page into a reversible masked layer (offline; FR-31/32/33)."""
    config = MaskConfig(pad=pad, border=border)
    with _open_store(path) as store:
        save_render_config(store, config)
        artifacts = mask_pages(
            store,
            mask=lambda image_path, boxes: mask_file(image_path, boxes, config),
            signature=config.signature(),
            force=force,
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
) -> None:
    """Run the processing pipeline."""
    with _open_store(path) as store:
        pipeline = build_pipeline(store)
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
        composite_pages(store, composite=composite_page_file, signature=COMPOSITE_SIGNATURE)
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
def review(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
    host: Annotated[str, typer.Option(help="Host to bind the local server to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to serve on.")] = 8000,
) -> None:
    """Launch the local web review editor (FR-36; §13)."""
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
    try:
        url = f"http://{host}:{port}"
        typer.secho(f"mfo review serving {path} at {url}", fg=typer.colors.GREEN)
        typer.echo("Press Ctrl+C to stop.")
        serve(store, host=host, port=port)
    finally:
        store.close()
