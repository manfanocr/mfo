"""Concrete pipeline stages and their persisted configuration (§10; FR-5, FR-48, MVP-10).

The CLI is the composition root: it is the one layer allowed to depend on both ``vision`` and
``storage``, so the stages that glue a pure transform to persistence live here. Each stage
implements the :class:`~mfo.core.pipeline.Stage` protocol over a :class:`ProjectStore` context
and does its work through the same idempotent storage/vision functions the standalone ``import``
and ``preprocess`` commands use.

A stage needs to be replayable from a freshly reopened project, so the inputs it depends on
(the import source, the preprocess knobs) are persisted in ``Project.config``. ``mfo run`` then
rebuilds the pipeline from that config and resumes: a stage interrupted before its completion
record was written simply has no record and runs again, and its underlying operation skips the
work already done (pages already copied, derivatives already cached).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from mfo.core.pipeline import Pipeline, Stage
from mfo.storage import (
    ProjectStore,
    detect_regions,
    import_pages,
    preprocess_pages,
)
from mfo.vision import (
    PageOrder,
    PreprocessConfig,
    RegionDetector,
    detect_file,
    discover_images,
    get_detector,
    preprocess_file,
)

IMPORT_STAGE = "import"
PREPROCESS_STAGE = "preprocess"
DETECT_STAGE = "detect"


class ImportStage:
    """Discover and import the configured source directory into the project (idempotent)."""

    name = IMPORT_STAGE
    deps: tuple[str, ...] = ()

    def __init__(self, source: Path, *, order: PageOrder, manifest_order: list[str] | None) -> None:
        self._source = source
        self._order = order
        self._manifest_order = manifest_order

    def inputs_hash(self, ctx: ProjectStore) -> str:
        digest = hashlib.sha256()
        digest.update(str(self._source).encode("utf-8"))
        digest.update(self._order.value.encode("utf-8"))
        for name in self._manifest_order or ():
            digest.update(b"\x00")
            digest.update(name.encode("utf-8"))
        # Fold in the current source listing so newly added pages re-trigger the import.
        if self._source.is_dir():
            for name in sorted(p.name for p in self._source.iterdir() if p.is_file()):
                digest.update(b"\x01")
                digest.update(name.encode("utf-8"))
        return digest.hexdigest()

    def run(self, ctx: ProjectStore) -> None:
        scan = discover_images(self._source, order=self._order, manifest_order=self._manifest_order)
        import_pages(ctx, scan.images)


class PreprocessStage:
    """Build normalized analysis derivatives for the imported pages (idempotent)."""

    name = PREPROCESS_STAGE
    deps: tuple[str, ...] = (IMPORT_STAGE,)

    def __init__(self, config: PreprocessConfig) -> None:
        self._config = config

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return self._config.signature()

    def run(self, ctx: ProjectStore) -> None:
        preprocess_pages(
            ctx,
            transform=lambda path: preprocess_file(path, self._config),
            signature=self._config.signature(),
        )


class DetectStage:
    """Detect text regions on each page and persist them (idempotent, source-space coords)."""

    name = DETECT_STAGE
    deps: tuple[str, ...] = (PREPROCESS_STAGE,)

    def __init__(self, detector: RegionDetector) -> None:
        self._detector = detector

    def _signature(self) -> str:
        return f"{self._detector.name}@{self._detector.version}"

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return self._signature()

    def run(self, ctx: ProjectStore) -> None:
        detect_regions(
            ctx,
            detect=lambda path: detect_file(path, self._detector),
            signature=self._signature(),
        )


def save_import_config(
    store: ProjectStore,
    *,
    source: Path,
    order: PageOrder,
    manifest_order: list[str] | None,
) -> None:
    """Persist the import source so ``mfo run`` can replay/resume the import (FR-48)."""
    config = dict(store.project.config)
    config["import"] = {
        "source": str(source.resolve()),
        "order": order.value,
        "manifest": manifest_order,
    }
    store.set_project(store.project.model_copy(update={"config": config}))


def save_preprocess_config(store: ProjectStore, config: PreprocessConfig) -> None:
    """Persist the preprocessing knobs so ``mfo run`` reproduces the same derivatives (FR-48)."""
    project_config = dict(store.project.config)
    project_config["preprocess"] = {
        "grayscale": config.grayscale,
        "max_dimension": config.max_dimension,
        "denoise": config.denoise,
        "deskew": config.deskew,
    }
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_detect_config(store: ProjectStore, detector: str) -> None:
    """Persist the chosen detector so ``mfo run`` uses the same one (NFR-17, FR-48)."""
    project_config = dict(store.project.config)
    project_config["detect"] = {"detector": detector}
    store.set_project(store.project.model_copy(update={"config": project_config}))


def build_pipeline(store: ProjectStore) -> Pipeline[ProjectStore]:
    """Assemble the pipeline from the project's persisted configuration.

    The import stage only exists once a source has been configured (via ``mfo import``); the
    preprocess and detect stages use their saved config if present, otherwise defaults. Later
    milestones register ocr → structure → translate → render here.
    """
    stages: list[Stage[ProjectStore]] = []
    config = store.project.config

    import_config = config.get("import")
    if import_config is not None:
        stages.append(
            ImportStage(
                Path(import_config["source"]),
                order=PageOrder(import_config["order"]),
                manifest_order=import_config.get("manifest"),
            )
        )
        preprocess_config = config.get("preprocess") or {}
        stages.append(PreprocessStage(PreprocessConfig(**preprocess_config)))

        detect_config = config.get("detect") or {}
        stages.append(DetectStage(get_detector(detect_config.get("detector", "baseline"))))

    return Pipeline(stages)
