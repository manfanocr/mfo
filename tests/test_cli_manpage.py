"""Tests for the generated CLI man page (B8.11 closeout)."""

from __future__ import annotations

from mfo.cli.manpage import render_manpage


def test_manpage_has_standard_sections_and_is_deterministic() -> None:
    page = render_manpage(date="2026-01-01")
    again = render_manpage(date="2026-01-01")
    assert page == again  # deterministic for a fixed date
    assert page.startswith('.TH MFO 1 "2026-01-01"')
    for section in (".SH NAME", ".SH SYNOPSIS", ".SH COMMANDS", ".SH ENVIRONMENT", ".SH FILES"):
        assert section in page
    assert page.endswith("\n")


def test_manpage_documents_commands_options_and_env() -> None:
    page = render_manpage(date="2026-01-01")
    # A representative command, one of its options, a subcommand, and an env var all appear.
    assert ".B mfo detect [OPTIONS] PATH" in page
    assert "--detector" in page
    assert "mfo models pull" in page
    assert "MFO_MODEL_DIR" in page
