"""CLI layer: headless, scriptable entry point.

This is a placeholder entry point. The full command set
(``init``, ``run``, ``status``, ``review``, ``export``) lands in batch 0.4.
"""

from __future__ import annotations

import sys

from mfo import __version__


def main(argv: list[str] | None = None) -> int:
    """mfo command-line entry point (placeholder).

    Returns a process exit code. The real command surface arrives in batch 0.4.
    """
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"-V", "--version", "version"}:
        print(f"mfo {__version__}")
        return 0
    print(f"mfo {__version__} — CLI not yet implemented (see PLAN.md, batch 0.4).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
