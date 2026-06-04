#!/usr/bin/env bash
# Regenerate the generated docs from source: the CLI man page and the pdoc API reference.
# Both are derived artifacts — run this after changing CLI commands or public APIs.
#
#   man/mfo.1   troff man page (committed; install to your manpath, or `man -l man/mfo.1`)
#   docs/api/   HTML API reference (gitignored; open docs/api/index.html)
set -euo pipefail
cd "$(dirname "$0")/.."

python -m mfo.cli.manpage > man/mfo.1

# The package __init__ exports only __version__, so list the subpackages explicitly to document all.
pdoc -o docs/api \
  mfo.core mfo.vision mfo.language mfo.render mfo.storage mfo.cli mfo.ui

echo "Wrote man/mfo.1 and docs/api/ (open docs/api/index.html)."
