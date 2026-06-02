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

# How many units on either side count as "nearby" dialogue for context.
DEFAULT_NEIGHBOR_WINDOW = 1


def build_context(
    sources: Sequence[str],
    index: int,
    *,
    page_index: int,
    page_count: int,
    window: int = DEFAULT_NEIGHBOR_WINDOW,
) -> dict[str, Any]:
    """Assemble the context bundle for the unit at ``index`` among a page's ordered ``sources``.

    ``preceding``/``following`` carry up to ``window`` neighbouring source texts in reading order
    (empty strings dropped), giving the translator nearby dialogue; ``page_index``/``page_count``
    locate the page in the volume. The result is a plain JSON-serializable dict.
    """
    start = max(0, index - window)
    preceding = [text for text in sources[start:index] if text]
    following = [text for text in sources[index + 1 : index + 1 + window] if text]
    return {
        "page_index": page_index,
        "page_count": page_count,
        "preceding": preceding,
        "following": following,
    }
