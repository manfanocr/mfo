"""Stable identifier scheme for mfo entities.

IDs are ULIDs (https://github.com/ulid/spec): a 48-bit millisecond timestamp followed by
80 bits of randomness, rendered as 26 Crockford base32 characters. ULIDs are:

- **unique** — collision probability is negligible,
- **sortable** — lexicographic order matches creation order (the base32 alphabet is
  monotonic), which is convenient for stable display ordering,
- **opaque** — callers must treat them as opaque strings.

Each entity prefixes its ULID with a short type tag (e.g. ``rgn_01J...``) so IDs are
self-describing in logs, exports, and the traceability graph (invariant I-2). The generator is
dependency-free so the core layer stays lean and offline (I-8).
"""

from __future__ import annotations

import os
import time

# Crockford base32 alphabet (excludes I, L, O, U to avoid ambiguity).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH = 26
_TIMESTAMP_BITS = 48
_RANDOM_BITS = 80


def _encode(value: int, length: int) -> str:
    """Encode an unsigned integer as a fixed-length Crockford base32 string."""
    chars = [""] * length
    for i in range(length - 1, -1, -1):
        value, remainder = divmod(value, 32)
        chars[i] = _ALPHABET[remainder]
    return "".join(chars)


def new_ulid(timestamp_ms: int | None = None) -> str:
    """Generate a new 26-character ULID.

    ``timestamp_ms`` is exposed mainly for deterministic testing; when omitted the current
    wall-clock time is used.
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if not 0 <= timestamp_ms < (1 << _TIMESTAMP_BITS):
        raise ValueError(f"timestamp_ms out of ULID range: {timestamp_ms}")
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    value = (timestamp_ms << _RANDOM_BITS) | randomness
    return _encode(value, _ULID_LENGTH)


def new_id(prefix: str) -> str:
    """Generate a prefixed, self-describing entity ID, e.g. ``new_id("rgn")``."""
    if not prefix:
        raise ValueError("prefix must be a non-empty string")
    return f"{prefix}_{new_ulid()}"
