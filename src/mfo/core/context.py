"""Build the translation context bundle for a unit (spec §10.6, §12.5; FR-22, NFR-2).

Context-aware translation means a unit is not translated as an isolated line: the surrounding
dialogue on the page and the page's position in the volume travel with it (FR-22). This module is
pure — it folds already-assembled neighbour source texts and a page/chapter locator into a plain,
serializable dict — so it has no I/O and no dependency on storage or any provider. The bundle is
persisted on :class:`~mfo.core.models.TranslationUnit.context_bundle` and is the seam the offline
translator and the later AI adapters (M7, §12.5) read from.

We model the project as a single volume, so the page index within the page count is the chapter
locator; richer chapter/character-memory context is layered in later milestones.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# How many units on either side count as "nearby" dialogue for context. With one translation unit
# per bubble (the grouping default), this is the conversational window the context-aware translator
# sees around each line; a wider window gives more surrounding dialogue (FR-22).
DEFAULT_NEIGHBOR_WINDOW = 2


def build_context(
    sources: Sequence[str],
    index: int,
    *,
    page_index: int,
    page_count: int,
    window: int = DEFAULT_NEIGHBOR_WINDOW,
    panels: Sequence[int | None] | None = None,
) -> dict[str, Any]:
    """Assemble the context bundle for the unit at ``index`` among a page's ordered ``sources``.

    ``preceding``/``following`` carry up to ``window`` neighbouring source texts in reading order
    (empty strings dropped), giving the translator nearby dialogue; ``page_index``/``page_count``
    locate the page in the volume. The result is a plain JSON-serializable dict.

    When ``panels`` is given (one panel id per source, parallel to ``sources``) the neighbour window
    is scoped to the unit's own panel (SG-1, §12.5): only same-panel units count as neighbours, so
    context no longer bleeds across frames, and the unit's ``panel`` is recorded in the bundle.
    Units outside every panel (``panel`` is ``None``) keep the plain reading-order window. The
    grouping stays a *context* refinement — units are never merged (one bubble = one unit).
    """
    if panels is None:
        start = max(0, index - window)
        preceding = [text for text in sources[start:index] if text]
        following = [text for text in sources[index + 1 : index + 1 + window] if text]
        return {
            "page_index": page_index,
            "page_count": page_count,
            "preceding": preceding,
            "following": following,
        }

    panel = panels[index]
    preceding = _panel_neighbours(sources, panels, index, panel, window, step=-1)
    following = _panel_neighbours(sources, panels, index, panel, window, step=1)
    return {
        "page_index": page_index,
        "page_count": page_count,
        "panel": panel,
        "preceding": preceding,
        "following": following,
    }


def _panel_neighbours(
    sources: Sequence[str],
    panels: Sequence[int | None],
    index: int,
    panel: int | None,
    window: int,
    *,
    step: int,
) -> list[str]:
    """Up to ``window`` neighbour texts walking outward from ``index`` in direction ``step``.

    With a known ``panel`` the walk stops at the first unit in a different panel, keeping context
    inside the frame; an out-of-panel unit (``panel is None``) keeps the plain reading-order window.
    """
    out: list[str] = []
    j = index + step
    while 0 <= j < len(sources) and len(out) < window:
        if panel is not None and panels[j] != panel:
            break
        if sources[j]:
            out.append(sources[j])
        j += step
    return out if step > 0 else out[::-1]
