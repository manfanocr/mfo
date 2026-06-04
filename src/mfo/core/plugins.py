"""Entry-point plugin discovery for adapter registries (NFR-17, NFR-19, SG-9; spec §14.3).

Every adapter layer (detect / OCR / translate / assist / render) keeps a built-in registry of
factories keyed by a short config name. This module turns those registries into a documented,
*extensible* API: a third-party package can register an extra adapter via Python **entry points**
(declared in its packaging metadata) without editing mfo. The ``get_*`` resolvers consult their
built-ins first, then entry points, so the offline built-ins always resolve and can never be
shadowed by an installed plugin (I-7/I-8). A broken plugin is skipped with a warning, never fatal
(NFR-9) — a third-party package must not be able to break mfo's core path.

See ``docs/PLUGINS.md`` for the contributor contract (group names + factory signatures).
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping
from importlib.metadata import entry_points
from typing import TypeVar, cast

T = TypeVar("T")

# Entry-point group names. A third-party package registers an adapter by declaring an entry point
# under one of these groups, e.g. ``[project.entry-points."mfo.detectors"]``.
DETECTOR_GROUP = "mfo.detectors"
OCR_GROUP = "mfo.ocr"
TRANSLATOR_GROUP = "mfo.translators"
TRANSLITERATOR_GROUP = "mfo.transliterators"
SFX_CLASSIFIER_GROUP = "mfo.sfx_classifiers"
ASSISTANT_GROUP = "mfo.assistants"
RENDERER_GROUP = "mfo.renderers"


def discover_plugins(group: str) -> dict[str, Callable[..., object]]:
    """Return ``{name: factory}`` for every entry point registered under ``group``.

    A plugin whose entry point fails to import/load is skipped with a :class:`UserWarning` rather
    than raising, so one bad third-party package cannot break adapter resolution (NFR-9).
    """
    plugins: dict[str, Callable[..., object]] = {}
    for ep in entry_points(group=group):
        try:
            plugins[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not crash resolution
            warnings.warn(
                f"skipping plugin {ep.name!r} in group {group!r}: {exc}",
                stacklevel=2,
            )
    return plugins


def resolve_factory(
    name: str,
    builtins: Mapping[str, Callable[..., T]],
    group: str,
    *,
    kind: str,
) -> Callable[..., T]:
    """Resolve an adapter factory by ``name``: built-ins first, then ``group`` entry points.

    Built-ins take precedence so the offline defaults always win and cannot be shadowed. Raises
    ``ValueError`` (listing every known name) if ``name`` matches neither a built-in nor a plugin.
    """
    if name in builtins:
        return builtins[name]
    plugins = discover_plugins(group)
    if name in plugins:
        return cast("Callable[..., T]", plugins[name])
    known = ", ".join(sorted({*builtins, *plugins}))
    raise ValueError(f"unknown {kind} {name!r}; available: {known}")
