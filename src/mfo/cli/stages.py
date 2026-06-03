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
import json
from pathlib import Path

from mfo.core.enums import ReadingDirection, TranslationStyle
from mfo.core.glossary import GlossaryEntry, entries_from_config, entries_to_config
from mfo.core.grouping import DEFAULT_GAP_RATIO
from mfo.core.pipeline import Pipeline, Stage
from mfo.language import TranslationRequest, Translator, get_translator
from mfo.render import (
    CompositeArtifact,
    MaskConfig,
    Placement,
    composite_file,
    get_preset,
    mask_file,
)
from mfo.storage import (
    PagePlacement,
    ProjectStore,
    assign_reading_order,
    composite_pages,
    detect_regions,
    group_into_units,
    import_pages,
    mask_pages,
    ocr_regions,
    preprocess_pages,
    translate_units,
)
from mfo.vision import (
    OCREngine,
    PageOrder,
    PreprocessConfig,
    RegionDetector,
    detect_file,
    discover_images,
    get_detector,
    get_ocr_engine,
    preprocess_file,
    recognize_file,
)

IMPORT_STAGE = "import"
PREPROCESS_STAGE = "preprocess"
DETECT_STAGE = "detect"
STRUCTURE_STAGE = "structure"
GROUP_STAGE = "group"
OCR_STAGE = "ocr"
TRANSLATE_STAGE = "translate"
RENDER_STAGE = "render"
COMPOSITE_STAGE = "composite"

COMPOSITE_SIGNATURE = "composite@1"


def composite_page_file(base_path: Path, placements: list[PagePlacement]) -> CompositeArtifact:
    """Adapter binding storage's placement data to the render compositor (composition root).

    Resolves each placement's named style preset and typesets it onto the page, returning the
    composited PNG bytes that the storage stage persists. Keeps storage free of any image/PIL
    dependency (NFR-17) while the CLI wires the two layers together.
    """
    return composite_file(
        base_path,
        [Placement(text=p.text, box=p.bbox, preset=get_preset(p.preset)) for p in placements],
    )


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


class StructureStage:
    """Assign each region a reading-order index (idempotent, offline, geometry-only)."""

    name = STRUCTURE_STAGE
    deps: tuple[str, ...] = (DETECT_STAGE,)

    def __init__(self, direction: ReadingDirection) -> None:
        self._direction = direction

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return f"reading-order@1|{self._direction.value}"

    def run(self, ctx: ProjectStore) -> None:
        assign_reading_order(ctx, direction=self._direction)


class GroupStage:
    """Group each page's ordered regions into translation units (idempotent, offline, no OCR)."""

    name = GROUP_STAGE
    deps: tuple[str, ...] = (STRUCTURE_STAGE,)

    def __init__(self, max_gap_ratio: float) -> None:
        self._max_gap_ratio = max_gap_ratio

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return f"grouping@1|{self._max_gap_ratio}"

    def run(self, ctx: ProjectStore) -> None:
        group_into_units(ctx, max_gap_ratio=self._max_gap_ratio)


class OcrStage:
    """Recognize text on each region and persist it as OCR spans (idempotent, OCR ≠ translation)."""

    name = OCR_STAGE
    deps: tuple[str, ...] = (DETECT_STAGE,)

    def __init__(self, engine: OCREngine) -> None:
        self._engine = engine

    def _signature(self) -> str:
        return f"{self._engine.name}@{self._engine.version}"

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return self._signature()

    def run(self, ctx: ProjectStore) -> None:
        ocr_regions(
            ctx,
            recognize=lambda path, bbox: recognize_file(path, bbox, self._engine),
            signature=self._signature(),
        )


class TranslateStage:
    """Translate grouped units with context/glossary/style, storing candidates (idempotent)."""

    name = TRANSLATE_STAGE
    deps: tuple[str, ...] = (GROUP_STAGE, OCR_STAGE)

    def __init__(
        self,
        translator: Translator,
        *,
        source_lang: str,
        target_lang: str,
        style: TranslationStyle = TranslationStyle.BALANCED,
        glossary: tuple[GlossaryEntry, ...] = (),
    ) -> None:
        self._translator = translator
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._style = style
        self._glossary = glossary

    def _signature(self) -> str:
        return f"{self._translator.name}@{self._translator.version}"

    def _glossary_signature(self) -> str:
        payload = json.dumps(entries_to_config(self._glossary), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return (
            f"translate@2|{self._signature()}|{self._source_lang}->{self._target_lang}"
            f"|{self._style.value}|{self._glossary_signature()}"
        )

    def run(self, ctx: ProjectStore) -> None:
        translate_units(
            ctx,
            translate=lambda source, context: self._translator.translate(
                TranslationRequest(
                    source=source,
                    source_lang=self._source_lang,
                    target_lang=self._target_lang,
                    context=context,
                    style=self._style,
                )
            ),
            signature=self._signature(),
            target_lang=self._target_lang,
            style=self._style,
            glossary=self._glossary,
        )


class RenderStage:
    """Mask the source text on each page, producing a masked layer + mask (idempotent, offline).

    Masking depends only on the detected region geometry (not OCR/translation), so it joins right
    after detection and produces the reversible masked base (FR-31/32/33, I-1/I-6). The separate
    :class:`CompositeStage` later typesets the translated text onto that masked layer.
    """

    name = RENDER_STAGE
    deps: tuple[str, ...] = (DETECT_STAGE,)

    def __init__(self, config: MaskConfig) -> None:
        self._config = config

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return self._config.signature()

    def run(self, ctx: ProjectStore) -> None:
        mask_pages(
            ctx,
            mask=lambda path, boxes: mask_file(path, boxes, self._config),
            signature=self._config.signature(),
        )


class CompositeStage:
    """Typeset the selected translations onto each masked page, producing the final render.

    Compositing needs both the masked base (the render/mask stage) and the chosen translations
    (the translate stage), so it depends on both and joins the pipeline once both are configured.
    """

    name = COMPOSITE_STAGE
    deps: tuple[str, ...] = (RENDER_STAGE, TRANSLATE_STAGE)

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return COMPOSITE_SIGNATURE

    def run(self, ctx: ProjectStore) -> None:
        composite_pages(ctx, composite=composite_page_file, signature=COMPOSITE_SIGNATURE)


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


def save_structure_config(store: ProjectStore, direction: ReadingDirection) -> None:
    """Persist the reading direction so ``mfo run`` reproduces the same order (FR-17, FR-48)."""
    project_config = dict(store.project.config)
    project_config["structure"] = {"direction": direction.value}
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_group_config(store: ProjectStore, max_gap_ratio: float) -> None:
    """Persist the grouping knob so ``mfo run`` reproduces the same units (FR-19, FR-48)."""
    project_config = dict(store.project.config)
    project_config["group"] = {"max_gap_ratio": max_gap_ratio}
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_ocr_config(store: ProjectStore, engine: str) -> None:
    """Persist the chosen OCR engine so ``mfo run`` uses the same one (NFR-17, FR-48)."""
    project_config = dict(store.project.config)
    project_config["ocr"] = {"engine": engine}
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_translate_config(
    store: ProjectStore, translator: str, *, style: TranslationStyle = TranslationStyle.BALANCED
) -> None:
    """Persist the translator and style so ``mfo run`` reproduces them (NFR-17, FR-25, FR-48)."""
    project_config = dict(store.project.config)
    project_config["translate"] = {"translator": translator, "style": style.value}
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_render_config(store: ProjectStore, config: MaskConfig) -> None:
    """Persist the masking knobs so ``mfo run`` reproduces the same masked layers (FR-48)."""
    project_config = dict(store.project.config)
    project_config["render"] = {"pad": config.pad, "border": config.border}
    store.set_project(store.project.model_copy(update={"config": project_config}))


def load_glossary(store: ProjectStore) -> tuple[GlossaryEntry, ...]:
    """Read the project glossary from its persisted config (FR-24)."""
    return entries_from_config(store.project.config.get("glossary"))


def save_glossary(store: ProjectStore, entries: tuple[GlossaryEntry, ...]) -> None:
    """Persist the project glossary so translation and ``mfo run`` enforce it (FR-24, FR-48)."""
    project_config = dict(store.project.config)
    project_config["glossary"] = entries_to_config(entries)
    store.set_project(store.project.model_copy(update={"config": project_config}))


def build_pipeline(store: ProjectStore) -> Pipeline[ProjectStore]:
    """Assemble the pipeline from the project's persisted configuration.

    The import stage only exists once a source has been configured (via ``mfo import``); the
    preprocess, detect, structure (reading-order), and group (dialogue-chain) stages use their saved
    config if present, otherwise their zero-dependency defaults — structure and grouping are
    geometry-only so they stay on the offline path. OCR is opt-in: its default engine (manga-ocr) is
    an optional install, so the OCR stage joins the pipeline only once an engine has been chosen via
    ``mfo ocr``. Translation (consuming both the OCR text and the groups) is likewise opt-in via
    ``mfo translate`` and only joins once OCR is configured, since it depends on it. Rendering
    (masking) needs only the detected regions, so it joins independently once configured via
    ``mfo render``. Compositing — typesetting the chosen translations onto the masked page — needs
    both, so it joins last, once rendering and translation are both configured.
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

        structure_config = config.get("structure") or {}
        direction = ReadingDirection(
            structure_config.get("direction", store.project.reading_direction.value)
        )
        stages.append(StructureStage(direction))

        group_config = config.get("group") or {}
        stages.append(GroupStage(group_config.get("max_gap_ratio", DEFAULT_GAP_RATIO)))

        # Rendering (masking) only needs the detected regions, so it joins independently of OCR.
        render_config = config.get("render")
        if render_config is not None:
            stages.append(RenderStage(MaskConfig(**render_config)))

        ocr_config = config.get("ocr")
        if ocr_config is not None:
            stages.append(OcrStage(get_ocr_engine(ocr_config.get("engine", "manga-ocr"))))

            # Translation depends on OCR, so it only joins once OCR is configured too.
            translate_config = config.get("translate")
            if translate_config is not None:
                style = TranslationStyle(
                    translate_config.get("style", TranslationStyle.BALANCED.value)
                )
                stages.append(
                    TranslateStage(
                        get_translator(translate_config.get("translator", "argos")),
                        source_lang=store.project.source_lang,
                        target_lang=store.project.target_lang,
                        style=style,
                        glossary=entries_from_config(config.get("glossary")),
                    )
                )

                # Compositing needs both the masked base and the translations, so it joins only
                # once rendering (masking) has been configured alongside translation.
                if render_config is not None:
                    stages.append(CompositeStage())

    return Pipeline(stages)
