"""The ``mfo`` command-line application (spec FR-46, FR-47).

Commands are deliberately thin: they resolve configuration and a project, then delegate to the
core/storage/vision layers. ``init``, ``import``, ``status``, and ``run`` (the pipeline
orchestrator) are functional; ``export`` and ``review`` are wired to a real project but their
processing bodies arrive in later milestones (batches 5.3, 6.2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from mfo import __version__
from mfo.cli.config import build_settings
from mfo.cli.logging import configure_logging, get_logger
from mfo.core import (
    OCRSpan,
    Page,
    Project,
    ReadingDirection,
    Region,
    RenderArtifact,
    TranslationUnit,
)
from mfo.core.pipeline import Pipeline, Stage
from mfo.storage import JsonStateStore, ProjectStore, import_pages, preprocess_pages
from mfo.vision import PageOrder, PreprocessConfig, discover_images, preprocess_file

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
        try:
            scan = discover_images(source, order=order, manifest_order=manifest_order)
        except NotADirectoryError:
            typer.secho(f"Not a directory: {source}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from None
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
        pages = preprocess_pages(
            store,
            transform=lambda image_path: preprocess_file(image_path, config),
            signature=config.signature(),
            force=force,
        )
    typer.secho(f"Preprocessed {len(pages)} page(s).", fg=typer.colors.GREEN)


def _stage_line(label: str, count: int, unit: str) -> str:
    mark = "✓" if count else "·"
    state = f"{count} {unit}" if count else "pending"
    return f"  [{mark}] {label:<10} {state}"


@app.command()
def status(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
) -> None:
    """Show project info and per-stage progress."""
    with _open_store(path) as store:
        project = store.project
        pages = store.db.list(Page)
        preprocessed = sum(1 for page in pages if page.preprocessing.get("cache_key"))
        regions = store.db.list(Region)
        ocr_spans = store.db.list(OCRSpan)
        units = store.db.list(TranslationUnit)
        translated = sum(1 for unit in units if unit.selected_candidate_id is not None)
        renders = store.db.list(RenderArtifact)

    typer.secho(f"{project.name}  ({project.id})", bold=True)
    typer.echo(f"  languages: {project.source_lang} -> {project.target_lang}")
    typer.echo(f"  direction: {project.reading_direction}")
    typer.echo("")
    typer.echo("Stages:")
    typer.echo(_stage_line("import", len(pages), "pages"))
    typer.echo(_stage_line("preprocess", preprocessed, "pages"))
    typer.echo(_stage_line("detect", len(regions), "regions"))
    typer.echo(_stage_line("ocr", len(ocr_spans), "spans"))
    typer.echo(_stage_line("translate", translated, "units"))
    typer.echo(_stage_line("render", len(renders), "pages"))


def _not_yet(feature: str, batch: str) -> None:
    typer.secho(
        f"{feature} is not implemented yet (planned in batch {batch}).",
        fg=typer.colors.YELLOW,
    )


def _build_pipeline() -> Pipeline[ProjectStore]:
    """Assemble the processing pipeline.

    Stages (import → preprocess → detect → ocr → structure → translate → render) are
    registered as their milestones land; until then the pipeline is empty but fully wired.
    """
    stages: list[Stage[ProjectStore]] = []
    return Pipeline(stages)


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
        pipeline = _build_pipeline()
        if not pipeline.stage_names():
            typer.secho(
                "No pipeline stages are implemented yet (they land from milestone M1 onward).",
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
) -> None:
    """Export translated pages and mappings (placeholder)."""
    with _open_store(path):
        pass
    _not_yet("Export", "5.3")


@app.command()
def review(
    path: Annotated[Path, typer.Argument(help="Project directory.")],
) -> None:
    """Launch the local review editor (placeholder)."""
    with _open_store(path):
        pass
    _not_yet("The review editor", "6.2")
