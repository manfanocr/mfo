"""Crash-safe, on-disk persistence of pipeline stage records (FR-5, I-5, NFR-10/11).

Implements the ``core.pipeline.StateStore`` protocol by serializing stage records to a JSON
file under the project's ``logs/`` directory. Writes are atomic so an interrupted run never
corrupts the resume state; a missing file simply means "nothing has run yet".
"""

from __future__ import annotations

import json
from pathlib import Path

from mfo.core.pipeline import StageRecord
from mfo.storage.atomic import atomic_write_text


class JsonStateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, StageRecord]:
        if not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {name: StageRecord.from_dict(data) for name, data in raw.items()}

    def save(self, record: StageRecord) -> None:
        records = self.load()
        records[record.name] = record
        payload = {name: rec.to_dict() for name, rec in records.items()}
        atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True))
