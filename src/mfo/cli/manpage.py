"""Generate the ``mfo.1`` man page from the live Typer/Click command tree (NFR-12).

Keeping the man page *generated* from the same command definitions the CLI runs means it can't drift
from the actual options. Run ``python -m mfo.cli.manpage > man/mfo.1`` to regenerate it. The output
is plain troff (man macros), so it needs no external tooling to build or view
(``man -l man/mfo.1``).
"""

from __future__ import annotations

import datetime as _dt

import click
import typer.main

from mfo import __version__
from mfo.cli.app import app

_DESCRIPTION = (
    "mfo (Manhua Fanyi OCR) turns a folder of manga/manhua page images into translated, typeset "
    "pages. It runs a local-first, inspectable pipeline \\- import, detect, OCR, reading order, "
    "dialogue grouping, context-aware translation, masking and rendering \\- with an interactive "
    "review editor and full source-to-translation traceability. The core path is fully offline; "
    "cloud/LLM adapters are strictly opt-in."
)

# (variable, description) pairs for the ENVIRONMENT section. These are read directly by the code;
# keep them in sync with USER_GUIDE.md.
_ENVIRONMENT: tuple[tuple[str, str], ...] = (
    (
        "MFO_MODEL_DIR",
        "Directory optional model weights are cached in (default ~/.cache/mfo/models).",
    ),
    (
        "MFO_DETECTOR_MODEL_URL",
        "Download URL for the ONNX detector weight (used by `mfo models pull`).",
    ),
    (
        "MFO_API_KEY, MFO_API_URL, MFO_API_MODEL",
        "OpenAI-compatible translation/assist adapter config.",
    ),
    ("MFO_DEEPL_API_KEY, MFO_DEEPL_API_URL", "DeepL translation adapter config (opt-in)."),
    (
        "MFO_AI_KEY, MFO_AI_URL, MFO_AI_MODEL",
        "AI assist/OCR-correction adapter config (falls back to MFO_API_*).",
    ),
)


def _esc(text: str) -> str:
    """Escape text for a troff line: backslashes, then a leading dot/quote that would be a macro."""
    text = text.replace("\\", "\\\\")
    if text[:1] in (".", "'"):
        text = "\\&" + text
    return text


def _usage(path: str, command: click.Command) -> str:
    """A one-line usage string: ``mfo <path> [OPTIONS] ARG [OPTIONAL]``."""
    parts = [f"mfo {path}".strip()]
    if any(p.param_type_name == "option" for p in command.params):
        parts.append("[OPTIONS]")
    for param in command.params:
        if isinstance(param, click.Argument):
            name = param.name.upper() if param.name else "ARG"
            parts.append(name if param.required else f"[{name}]")
    return " ".join(parts)


def _render_command(path: str, command: click.Command) -> list[str]:
    """Render one leaf command as a labelled paragraph with its options."""
    lines = [
        ".TP",
        f".B {_esc(_usage(path, command))}",
        _esc(command.get_short_help_str(limit=200)),
    ]
    options = [
        p for p in command.params if p.param_type_name == "option" and isinstance(p, click.Option)
    ]
    if options:
        lines.append(".RS")
        for opt in options:
            flags = ", ".join(opt.opts + opt.secondary_opts)
            lines += [".TP", f".B {_esc(flags)}", _esc(opt.help or "")]
        lines.append(".RE")
    return lines


def _walk(path: str, command: click.Command, out: list[str]) -> None:
    """Emit ``command`` (and, for a group, each of its subcommands) in sorted order."""
    if isinstance(command, click.Group):
        for name in sorted(command.commands):
            _walk(f"{path} {name}".strip(), command.commands[name], out)
    else:
        out.extend(_render_command(path, command))


def render_manpage(*, date: str | None = None) -> str:
    """Return the full ``mfo.1`` troff source, introspected from the Typer app."""
    root = typer.main.get_command(app)
    day = date or _dt.date.today().isoformat()
    lines = [
        f'.TH MFO 1 "{day}" "mfo {__version__}" "User Commands"',
        ".SH NAME",
        "mfo \\- manga/manhua OCR & context-aware translation pipeline",
        ".SH SYNOPSIS",
        ".B mfo",
        "[OPTIONS] COMMAND [ARGS]...",
        ".SH DESCRIPTION",
        _DESCRIPTION,
        ".SH COMMANDS",
    ]
    assert isinstance(root, click.Group)
    for name in sorted(root.commands):
        _walk(name, root.commands[name], lines)

    lines += [".SH ENVIRONMENT"]
    for var, desc in _ENVIRONMENT:
        lines += [".TP", f".B {_esc(var)}", _esc(desc)]

    lines += [
        ".SH FILES",
        ".TP",
        ".B <project>/manifest.json",
        "Human-readable project header (id, languages, config, page order).",
        ".TP",
        ".B <project>/project.db",
        "SQLite store: regions, OCR, translations, edits, renders, history, assignments.",
        ".TP",
        ".B ~/.cache/mfo/models",
        "Default cache for optional model weights (override with MFO_MODEL_DIR).",
        ".SH SEE ALSO",
        "Full documentation: README.md and docs/USER_GUIDE.md in the mfo distribution.",
        ".SH AUTHOR",
        "mfo contributors.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover - tiny CLI shim
    import sys

    sys.stdout.write(render_manpage())
