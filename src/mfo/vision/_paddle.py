"""Shared runtime hints for the optional PaddleOCR adapters (OCR engine + region detector).

PaddleOCR 3.x defaults to MKLDNN (oneDNN) on CPU, but under its new PIR executor some ops hit an
``Unimplemented`` error on a range of CPUs (``ConvertPirAttribute2RuntimeAttribute ... onednn``).
Selecting the plain CPU runtime avoids that crash at a small speed cost — the right trade for an
optional, fallback-protected stage. We set it via ``setdefault`` *before* paddle is imported (the
flag is read once at import time) so a user who knows their CPU is fine can re-enable MKLDNN by
exporting ``PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=True`` themselves.
"""

from __future__ import annotations

import os


def _prefer_paddle_cpu_runtime() -> None:
    """Default PaddleOCR to its plain CPU runtime (no MKLDNN), unless the user overrode it."""
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
