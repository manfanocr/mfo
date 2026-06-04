"""Per-series style presets — reusable, named project settings for a series (spec §17; SG-4).

A long-running series is many volumes, each its own mfo project, and they should look and read the
same: the same translation register, the same shared terminology, the same typesetting knobs. A
:class:`SeriesPreset` bundles those three decisions under one name so a new volume adopts them in a
single step (FR-25, FR-35):

* the translation **style** (the :class:`~mfo.core.enums.TranslationStyle` register from 4.2),
* a link to the shared **series glossary** (the cross-volume store from 8.5),
* the **render** knobs (the masking config from the render stage).

This module is pure (no I/O): the on-disk store that persists a named collection of presets, plus
applying one to a project, live in :mod:`mfo.storage.presets` and the CLI composition root.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from mfo.core.enums import TranslationStyle


class RenderPreset(BaseModel):
    """The render-stage knobs a series preset pins (the masking config; FR-35).

    ``pad``/``border`` mirror :class:`mfo.render.MaskConfig`'s defaults; they are duplicated here
    (rather than imported) so ``core`` keeps no dependency on the outer ``render`` layer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    pad: int = 2  # grow each masked box by this many px to catch anti-aliased text edges
    border: int = 4  # width (px) of the ring sampled to estimate the background fill


class SeriesPreset(BaseModel):
    """A named bundle of per-series settings: style + shared glossary + render config (SG-4)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # the preset's label, unique within a store
    style: TranslationStyle = TranslationStyle.BALANCED
    glossary_path: str | None = None  # path to the shared series-glossary store (8.5), if any
    render: RenderPreset = RenderPreset()


class SeriesPresetStore(BaseModel):
    """A persisted, named collection of :class:`SeriesPreset`s shared across a series' volumes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    presets: tuple[SeriesPreset, ...] = ()


def upsert_preset(store: SeriesPresetStore, preset: SeriesPreset) -> SeriesPresetStore:
    """Add ``preset`` to ``store`` (or replace the one of the same name); order preserved."""
    replaced = any(existing.name == preset.name for existing in store.presets)
    presets = (
        tuple(p if p.name != preset.name else preset for p in store.presets)
        if replaced
        else (*store.presets, preset)
    )
    return store.model_copy(update={"presets": presets})


def remove_preset(store: SeriesPresetStore, name: str) -> SeriesPresetStore:
    """Return ``store`` without the preset named ``name`` (a no-op if it is absent)."""
    return store.model_copy(update={"presets": tuple(p for p in store.presets if p.name != name)})


def find_preset(store: SeriesPresetStore, name: str) -> SeriesPreset | None:
    """The preset named ``name``, or ``None`` if the store has no such preset."""
    return next((p for p in store.presets if p.name == name), None)


def series_preset_names(store: SeriesPresetStore) -> list[str]:
    """The names of the presets in ``store``, in definition order."""
    return [p.name for p in store.presets]
