"""SQLite persistence for relational project data (spec §11.2).

Each entity type maps to a table holding the entity ``id``, its JSON ``data`` blob, and a few
extracted, indexed columns for querying (e.g. ``page_id``). Storing the model as JSON keeps the
schema aligned with the Pydantic models while the index columns support the lookups the
pipeline and review editor need.

Schema changes are applied through an ordered list of migrations tracked via SQLite's
``PRAGMA user_version`` (NFR-26, NFR-27). Migrations are idempotent so reopening a project is
always safe (FR-5, FR-48).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from mfo.core import (
    EditRecord,
    OCRSpan,
    Page,
    Region,
    RenderArtifact,
    TranslationUnit,
)
from mfo.core.models import MfoModel

T = TypeVar("T", bound=MfoModel)


@dataclass(frozen=True)
class _Table:
    name: str
    # Indexed columns: maps the SQL column name to the model attribute it is extracted from.
    index_columns: dict[str, str]


# Entity → table mapping. Only relational entities live in the DB; ``Project`` lives in the
# manifest, so attempting to store it raises (see :meth:`Database._table`).
_TABLES: dict[type[MfoModel], _Table] = {
    Page: _Table("pages", {"project_id": "project_id", "idx": "index"}),
    Region: _Table("regions", {"page_id": "page_id"}),
    OCRSpan: _Table("ocr_spans", {"region_id": "region_id"}),
    TranslationUnit: _Table("translation_units", {"page_id": "page_id"}),
    EditRecord: _Table("edit_records", {"translation_unit_id": "translation_unit_id"}),
    RenderArtifact: _Table("render_artifacts", {"page_id": "page_id"}),
}


def _migration_001(conn: sqlite3.Connection) -> None:
    """Create the base schema for every entity table."""
    for table in _TABLES.values():
        extra = "".join(
            f", {column} {'INTEGER' if column == 'idx' else 'TEXT'}"
            for column in table.index_columns
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {table.name} "
            f"(id TEXT PRIMARY KEY, data TEXT NOT NULL{extra})"
        )
        for column in table.index_columns:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table.name}_{column} ON {table.name} ({column})"
            )


def _migration_002(conn: sqlite3.Connection) -> None:
    """Index ``translation_units.page_id`` so dialogue grouping can query/recompute per page.

    A fresh database already has the column (``_migration_001`` builds from the current table
    definitions), so the ``ADD COLUMN`` is guarded; existing databases pick it up here. The index
    creation is idempotent either way.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(translation_units)")}
    if "page_id" not in columns:
        conn.execute("ALTER TABLE translation_units ADD COLUMN page_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_translation_units_page_id ON translation_units (page_id)"
    )


# Ordered list of migrations; the schema version is the number applied.
_MIGRATIONS = [_migration_001, _migration_002]
SCHEMA_VERSION = len(_MIGRATIONS)


def _migrate(conn: sqlite3.Connection) -> None:
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    for index in range(current, len(_MIGRATIONS)):
        _MIGRATIONS[index](conn)
        # user_version cannot be parameterized; ``index + 1`` is a trusted integer.
        conn.execute(f"PRAGMA user_version = {index + 1}")
    conn.commit()


class Database:
    """A connection to a project's SQLite database with typed entity CRUD."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: Path) -> Database:
        """Open (creating if needed) the database at ``path`` and apply pending migrations."""
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA foreign_keys = ON")
        _migrate(conn)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    # -- writes ---------------------------------------------------------------------------

    def save(self, entity: MfoModel) -> None:
        """Insert or replace a single entity, committing immediately (NFR-11)."""
        self._insert(entity)
        self._conn.commit()

    def save_all(self, entities: Iterable[MfoModel]) -> None:
        """Insert or replace many entities in one committed transaction."""
        for entity in entities:
            self._insert(entity)
        self._conn.commit()

    def _insert(self, entity: MfoModel) -> None:
        table = self._table(type(entity))
        columns = ["id", "data", *table.index_columns]
        values: list[object] = [entity.id, entity.model_dump_json()]
        values.extend(getattr(entity, attr) for attr in table.index_columns.values())
        placeholders = ", ".join(["?"] * len(columns))
        self._conn.execute(
            f"INSERT OR REPLACE INTO {table.name} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )

    def delete(self, model: type[T], *, where: tuple[str, object]) -> int:
        """Delete rows of ``model`` matching an indexed column. Returns the rows removed.

        ``where`` is validated against the table's known columns to stay injection-safe.
        """
        table = self._table(model)
        column, value = where
        self._require_column(column, {"id", *table.index_columns})
        cursor = self._conn.execute(f"DELETE FROM {table.name} WHERE {column} = ?", (value,))
        self._conn.commit()
        return cursor.rowcount

    # -- reads ----------------------------------------------------------------------------

    def get(self, model: type[T], entity_id: str) -> T | None:
        table = self._table(model)
        row = self._conn.execute(
            f"SELECT data FROM {table.name} WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            return None
        return model.model_validate_json(row[0])

    def list(
        self,
        model: type[T],
        *,
        where: tuple[str, object] | None = None,
        order_by: str | None = None,
    ) -> list[T]:
        """List entities, optionally filtered by an indexed column and/or ordered.

        ``where``/``order_by`` columns are validated against the table's known columns to keep
        the queries injection-safe.
        """
        table = self._table(model)
        allowed = {"id", *table.index_columns}
        clauses = [f"SELECT data FROM {table.name}"]
        params: list[object] = []
        if where is not None:
            column, value = where
            self._require_column(column, allowed)
            clauses.append(f"WHERE {column} = ?")
            params.append(value)
        if order_by is not None:
            self._require_column(order_by, allowed)
            clauses.append(f"ORDER BY {order_by}")
        rows = self._conn.execute(" ".join(clauses), params).fetchall()
        return [model.model_validate_json(row[0]) for row in rows]

    # -- helpers --------------------------------------------------------------------------

    @staticmethod
    def _table(model: type[MfoModel]) -> _Table:
        try:
            return _TABLES[model]
        except KeyError:
            raise TypeError(f"{model.__name__} is not a persisted entity type") from None

    @staticmethod
    def _require_column(column: str, allowed: set[str]) -> None:
        if column not in allowed:
            raise ValueError(f"unknown column {column!r}; allowed: {sorted(allowed)}")
