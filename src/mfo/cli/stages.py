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

from mfo.core.enums import AssistMode, ReadingDirection, RegionType, SfxMode, TranslationStyle
from mfo.core.glossary import (
    GlossaryEntry,
    entries_from_config,
    entries_to_config,
    merge_glossaries,
)
from mfo.core.grouping import DEFAULT_GAP_RATIO
from mfo.core.pipeline import Pipeline, Stage
from mfo.core.presets import SeriesPreset
from mfo.language import TranslationRequest, Translator, get_translator
from mfo.language.transliterate import Transliterator, get_transliterator
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
    load_series_glossary,
    mask_pages,
    ocr_regions,
    preprocess_pages,
    process_sfx,
    translate_units,
)
from mfo.vision import (
    DEFAULT_OVERLAP_FRAC,
    OCREngine,
    PageOrder,
    PreprocessConfig,
    RegionDetector,
    SfxClassifier,
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

IMPORT_STAGE = "import"
PREPROCESS_STAGE = "preprocess"
DETECT_STAGE = "detect"
STRUCTURE_STAGE = "structure"
GROUP_STAGE = "group"
OCR_STAGE = "ocr"
SFX_STAGE = "sfx"
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


def archive_extract_dir(store: ProjectStore, source: Path) -> Path:
    """Where an archive's images are staged inside the project cache (read-only source, I-1)."""
    return store.layout.cache_dir / "import" / Path(source).stem


class ImportStage:
    """Discover and import the configured source (directory or CBZ/ZIP) into the project.

    Idempotent and replayable from a reopened project: an archive's images are extracted into the
    project cache before discovery, and pages already copied are skipped (FR-5).
    """

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
        if self._source.is_dir():
            # Fold in the current source listing so newly added pages re-trigger the import.
            for name in sorted(p.name for p in self._source.iterdir() if p.is_file()):
                digest.update(b"\x01")
                digest.update(name.encode("utf-8"))
        elif is_archive(self._source) and self._source.is_file():
            # Fold in the archive's size + mtime so a changed archive re-triggers the import.
            stat = self._source.stat()
            digest.update(f"\x02{stat.st_size}:{stat.st_mtime_ns}".encode())
        return digest.hexdigest()

    def run(self, ctx: ProjectStore) -> None:
        extract_to = archive_extract_dir(ctx, self._source) if is_archive(self._source) else None
        scan = discover_images(
            self._source,
            order=self._order,
            manifest_order=self._manifest_order,
            extract_to=extract_to,
        )
        import_pages(ctx, scan.images)


class PreprocessStage:
    """Build normalized analysis derivatives for the imported pages (idempotent)."""

    name = PREPROCESS_STAGE
    deps: tuple[str, ...] = (IMPORT_STAGE,)

    def __init__(self, config: PreprocessConfig, *, jobs: int = 1) -> None:
        self._config = config
        self._jobs = jobs

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return self._config.signature()

    def run(self, ctx: ProjectStore) -> None:
        preprocess_pages(
            ctx,
            transform=lambda path: preprocess_file(path, self._config),
            signature=self._config.signature(),
            jobs=self._jobs,
        )


class DetectStage:
    """Detect text regions on each page and persist them (idempotent, source-space coords)."""

    name = DETECT_STAGE
    deps: tuple[str, ...] = (PREPROCESS_STAGE,)

    def __init__(self, detector: RegionDetector, *, jobs: int = 1) -> None:
        self._detector = detector
        self._jobs = jobs

    def _signature(self) -> str:
        return f"{self._detector.name}@{self._detector.version}"

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return self._signature()

    def run(self, ctx: ProjectStore) -> None:
        detect_regions(
            ctx,
            detect=lambda path: detect_file(path, self._detector),
            signature=self._signature(),
            jobs=self._jobs,
        )


class StructureStage:
    """Assign each region a reading-order index (idempotent; offline unless panel-aware).

    With ``panels`` enabled the order is refined frame-by-frame using best-effort panel detection
    (FR-18), which reads the page images; otherwise it stays the offline, geometry-only heuristic.
    """

    name = STRUCTURE_STAGE
    deps: tuple[str, ...] = (DETECT_STAGE,)

    def __init__(self, direction: ReadingDirection, *, panels: bool = False) -> None:
        self._direction = direction
        self._panels = panels

    def inputs_hash(self, ctx: ProjectStore) -> str:
        mode = "panels" if self._panels else "flat"
        return f"reading-order@2|{self._direction.value}|{mode}"

    def run(self, ctx: ProjectStore) -> None:
        assign_reading_order(
            ctx,
            direction=self._direction,
            detect_panels=detect_panels_file if self._panels else None,
        )


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

    def __init__(self, engine: OCREngine, *, jobs: int = 1) -> None:
        self._engine = engine
        self._jobs = jobs

    def _signature(self) -> str:
        return f"{self._engine.name}@{self._engine.version}"

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return self._signature()

    def run(self, ctx: ProjectStore) -> None:
        ocr_regions(
            ctx,
            recognize=lambda path, bbox: recognize_file(path, bbox, self._engine),
            signature=self._signature(),
            jobs=self._jobs,
        )


class SfxStage:
    """Classify SFX regions and attach SFX transliterations (idempotent, offline by default; SG-5).

    Needs the regions typed/grouped (to classify and to find SFX-led units) and the OCR text (to
    transliterate), so it depends on both the group and OCR stages. The render-time toggle (render /
    transliterate / skip) is honoured by the mask/composite stages; this stage only types regions
    and produces the transliteration candidate.
    """

    name = SFX_STAGE
    deps: tuple[str, ...] = (GROUP_STAGE, OCR_STAGE)

    def __init__(
        self,
        classifier: SfxClassifier,
        transliterator: Transliterator,
        *,
        source_lang: str,
        mode: SfxMode,
    ) -> None:
        self._classifier = classifier
        self._transliterator = transliterator
        self._source_lang = source_lang
        self._mode = mode

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return (
            f"sfx@1|{self._classifier.name}@{self._classifier.version}"
            f"|{self._transliterator.name}@{self._transliterator.version}|{self._mode.value}"
        )

    def run(self, ctx: ProjectStore) -> None:
        process_sfx(
            ctx,
            classify=lambda region, page: classify_region_type(
                SfxFeatures(
                    bbox=region.bbox,
                    region_type=region.type,
                    page_width=page.width,
                    page_height=page.height,
                ),
                self._classifier,
            ),
            transliterate=lambda text: self._transliterator.transliterate(
                text, source_lang=self._source_lang
            ),
            mode=self._mode,
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
        jobs: int = 1,
    ) -> None:
        self._translator = translator
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._style = style
        self._glossary = glossary
        self._jobs = jobs

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
            jobs=self._jobs,
        )


class RenderStage:
    """Mask the source text on each page, producing a masked layer + mask (idempotent, offline).

    Masking depends only on the detected region geometry (not OCR/translation), so it joins right
    after detection and produces the reversible masked base (FR-31/32/33, I-1/I-6). The separate
    :class:`CompositeStage` later typesets the translated text onto that masked layer.
    """

    name = RENDER_STAGE
    deps: tuple[str, ...] = (DETECT_STAGE,)

    def __init__(
        self,
        config: MaskConfig,
        *,
        skip_types: frozenset[RegionType] = frozenset(),
        extra_deps: tuple[str, ...] = (),
        jobs: int = 1,
    ) -> None:
        self._config = config
        self._skip_types = skip_types
        # Masking must run after SFX classification when the SFX mode is "skip", so the SFX regions
        # are typed before masking decides which boxes to leave untouched (SG-5). The CLI adds the
        # SFX stage as an extra dependency in that case.
        self.deps = (DETECT_STAGE, *extra_deps)
        self._jobs = jobs

    def _skip_token(self) -> str:
        return ",".join(sorted(t.value for t in self._skip_types))

    def inputs_hash(self, ctx: ProjectStore) -> str:
        return f"{self._config.signature()}|skip={self._skip_token()}"

    def run(self, ctx: ProjectStore) -> None:
        mask_pages(
            ctx,
            mask=lambda path, boxes: mask_file(path, boxes, self._config),
            signature=self._config.signature(),
            skip_types=self._skip_types,
            jobs=self._jobs,
        )


class CompositeStage:
    """Typeset the selected translations onto each masked page, producing the final render.

    Compositing needs both the masked base (the render/mask stage) and the chosen translations
    (the translate stage), so it depends on both and joins the pipeline once both are configured.
    """

    name = COMPOSITE_STAGE
    deps: tuple[str, ...] = (RENDER_STAGE, TRANSLATE_STAGE)

    def __init__(self, *, skip_types: frozenset[RegionType] = frozenset(), jobs: int = 1) -> None:
        self._skip_types = skip_types
        self._jobs = jobs

    def inputs_hash(self, ctx: ProjectStore) -> str:
        token = ",".join(sorted(t.value for t in self._skip_types))
        return f"{COMPOSITE_SIGNATURE}|skip={token}"

    def run(self, ctx: ProjectStore) -> None:
        composite_pages(
            ctx,
            composite=composite_page_file,
            signature=COMPOSITE_SIGNATURE,
            skip_types=self._skip_types,
            jobs=self._jobs,
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


def save_detect_config(
    store: ProjectStore,
    detector: str,
    *,
    merge_overlap: bool = True,
    overlap_frac: float = DEFAULT_OVERLAP_FRAC,
) -> None:
    """Persist the detector + overlap-merge knobs so ``mfo run`` reproduces detection (NFR-17)."""
    project_config = dict(store.project.config)
    project_config["detect"] = {
        "detector": detector,
        "merge_overlap": merge_overlap,
        "overlap_frac": overlap_frac,
    }
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_structure_config(
    store: ProjectStore, direction: ReadingDirection, *, panels: bool = False
) -> None:
    """Persist the reading direction + panel mode so ``mfo run`` reproduces the order (FR-17/18)."""
    project_config = dict(store.project.config)
    project_config["structure"] = {"direction": direction.value, "panels": panels}
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


def save_assist_config(
    store: ProjectStore,
    assistant: str,
    *,
    mode: AssistMode,
    min_confidence: float,
    style: TranslationStyle = TranslationStyle.BALANCED,
) -> None:
    """Persist the AI-assist choices so they are reproducible (NFR-17, FR-48, §12.4)."""
    project_config = dict(store.project.config)
    project_config["assist"] = {
        "assistant": assistant,
        "mode": mode.value,
        "min_confidence": min_confidence,
        "style": style.value,
    }
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_render_config(store: ProjectStore, config: MaskConfig) -> None:
    """Persist the masking knobs so ``mfo run`` reproduces the same masked layers (FR-48)."""
    project_config = dict(store.project.config)
    project_config["render"] = {"pad": config.pad, "border": config.border}
    store.set_project(store.project.model_copy(update={"config": project_config}))


def save_sfx_config(
    store: ProjectStore,
    *,
    mode: SfxMode,
    classifier: str = "heuristic",
    transliterator: str = "kana",
) -> None:
    """Persist the SFX mode + adapters so ``mfo run`` reproduces SFX handling (SG-5, FR-48)."""
    project_config = dict(store.project.config)
    project_config["sfx"] = {
        "mode": mode.value,
        "classifier": classifier,
        "transliterator": transliterator,
    }
    store.set_project(store.project.model_copy(update={"config": project_config}))


def sfx_skip_types(store: ProjectStore) -> frozenset[RegionType]:
    """Region types the render stages leave untouched: ``{SFX}`` in ``skip`` mode, else none (SG-5).

    With no SFX config (the default) this is empty, so masking/compositing behave exactly as before.
    """
    sfx_config = store.project.config.get("sfx")
    if sfx_config and sfx_config.get("mode") == SfxMode.SKIP.value:
        return frozenset({RegionType.SFX})
    return frozenset()


def load_glossary(store: ProjectStore) -> tuple[GlossaryEntry, ...]:
    """Read the project glossary from its persisted config (FR-24)."""
    return entries_from_config(store.project.config.get("glossary"))


def save_glossary(store: ProjectStore, entries: tuple[GlossaryEntry, ...]) -> None:
    """Persist the project glossary so translation and ``mfo run`` enforce it (FR-24, FR-48)."""
    project_config = dict(store.project.config)
    project_config["glossary"] = entries_to_config(entries)
    store.set_project(store.project.model_copy(update={"config": project_config}))


def series_glossary_path(store: ProjectStore) -> Path | None:
    """The shared series-glossary store this project links to, or ``None`` if unlinked (SG-2)."""
    raw = store.project.config.get("series_glossary")
    return Path(raw) if raw else None


def link_series_glossary(store: ProjectStore, path: Path) -> None:
    """Point this project at a shared series-glossary store so its volumes share terms (SG-2)."""
    project_config = dict(store.project.config)
    project_config["series_glossary"] = str(path)
    store.set_project(store.project.model_copy(update={"config": project_config}))


def apply_series_preset(store: ProjectStore, preset: SeriesPreset) -> None:
    """Apply a series preset to a project in one step: style, glossary link, render config (SG-4).

    Sets the translation style (preserving any already-chosen translator), links the shared series
    glossary if the preset names one, and persists the render (masking) config — so a new volume
    adopts the whole bundle with a single command (FR-25, FR-35).
    """
    translate_config = store.project.config.get("translate") or {}
    translator = translate_config.get("translator", "argos")
    save_translate_config(store, translator, style=preset.style)
    if preset.glossary_path:
        link_series_glossary(store, Path(preset.glossary_path))
    save_render_config(store, MaskConfig(pad=preset.render.pad, border=preset.render.border))


def load_effective_glossary(store: ProjectStore) -> tuple[GlossaryEntry, ...]:
    """The glossary a unit actually consults: project glossary over the linked series one (SG-2).

    Project entries win (:func:`~mfo.core.glossary.merge_glossaries`); with no linked series store
    this is just the project glossary, so the offline core path is unchanged for unlinked projects.
    """
    project = load_glossary(store)
    path = series_glossary_path(store)
    if path is None:
        return project
    return merge_glossaries(project, load_series_glossary(path).entries)


def build_pipeline(store: ProjectStore, *, jobs: int = 1) -> Pipeline[ProjectStore]:
    """Assemble the pipeline from the project's persisted configuration.

    ``jobs`` is a runtime performance knob (the per-page worker count for the heavy stages); it is
    deliberately kept out of every stage's ``inputs_hash`` so the cache key — and therefore which
    pages are skipped and the data produced — is identical regardless of the worker count (NFR-8).

    The import stage only exists once a source has been configured (via ``mfo import``); the
    preprocess, detect, structure (reading-order), and group (dialogue-chain) stages use their saved
    config if present, otherwise their zero-dependency defaults — grouping (and structure, unless
    its optional panel-aware mode is enabled) is geometry-only so it stays on the offline path. OCR
    is opt-in: its default engine (manga-ocr) is
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
        stages.append(PreprocessStage(PreprocessConfig(**preprocess_config), jobs=jobs))

        detect_config = config.get("detect") or {}
        stages.append(
            DetectStage(
                get_detector(
                    detect_config.get("detector", "baseline"),
                    lang=store.project.source_lang,
                    merge_overlap=detect_config.get("merge_overlap", True),
                    overlap_frac=detect_config.get("overlap_frac", DEFAULT_OVERLAP_FRAC),
                ),
                jobs=jobs,
            )
        )

        structure_config = config.get("structure") or {}
        direction = ReadingDirection(
            structure_config.get("direction", store.project.reading_direction.value)
        )
        stages.append(StructureStage(direction, panels=structure_config.get("panels", False)))

        group_config = config.get("group") or {}
        stages.append(GroupStage(group_config.get("max_gap_ratio", DEFAULT_GAP_RATIO)))

        # SFX handling (classify + transliterate) is opt-in and needs OCR, so it joins only when
        # both an OCR engine and an SFX mode are configured (via ``mfo sfx``). Masking must run
        # after it in "skip" mode so SFX regions are typed before masking leaves them untouched.
        ocr_config = config.get("ocr")
        sfx_config = config.get("sfx")
        sfx_active = sfx_config is not None and ocr_config is not None
        skip_types = sfx_skip_types(store) if sfx_active else frozenset()

        # Rendering (masking) only needs the detected regions, so it joins independently of OCR.
        render_config = config.get("render")
        if render_config is not None:
            stages.append(
                RenderStage(
                    MaskConfig(**render_config),
                    skip_types=skip_types,
                    extra_deps=(SFX_STAGE,) if sfx_active else (),
                    jobs=jobs,
                )
            )

        if ocr_config is not None:
            stages.append(
                OcrStage(
                    get_ocr_engine(
                        ocr_config.get("engine", "manga-ocr"), lang=store.project.source_lang
                    ),
                    jobs=jobs,
                )
            )

            if sfx_active:
                assert sfx_config is not None
                stages.append(
                    SfxStage(
                        get_sfx_classifier(sfx_config.get("classifier", "heuristic")),
                        get_transliterator(sfx_config.get("transliterator", "kana")),
                        source_lang=store.project.source_lang,
                        mode=SfxMode(sfx_config.get("mode", SfxMode.RENDER.value)),
                    )
                )

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
                        glossary=load_effective_glossary(store),
                        jobs=jobs,
                    )
                )

                # Compositing needs both the masked base and the translations, so it joins only
                # once rendering (masking) has been configured alongside translation.
                if render_config is not None:
                    stages.append(CompositeStage(skip_types=skip_types, jobs=jobs))

    return Pipeline(stages)
