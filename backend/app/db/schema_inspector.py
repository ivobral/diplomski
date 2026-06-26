"""Dinamički dohvat sheme baze."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Inspector
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import settings
from app.core.exceptions import SchemaInspectionError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    categorical_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ForeignKeyInfo:
    constrained_columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableInfo:
    name: str
    columns: tuple[ColumnInfo, ...]
    foreign_keys: tuple[ForeignKeyInfo, ...]
    sample_rows: tuple[tuple[Any, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class DatabaseSchema:
    tables: tuple[TableInfo, ...]
    fetched_at: float = field(default_factory=time.time)

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def find_table(self, name: str) -> TableInfo | None:
        lower = name.lower()
        for t in self.tables:
            if t.name.lower() == lower:
                return t
        return None


# ----------------------------------------------------------------------
# Schema Inspector
# ----------------------------------------------------------------------


class SchemaInspector:
    def __init__(self, engine: AsyncEngine, cache_ttl_seconds: int | None = None) -> None:
        self._engine = engine
        self._ttl = cache_ttl_seconds if cache_ttl_seconds is not None else settings.SCHEMA_CACHE_TTL_SECONDS
        self._cached: DatabaseSchema | None = None

    async def get_schema(self, force_refresh: bool = False, include_sample_rows: bool = False) -> DatabaseSchema:
        if not include_sample_rows and not force_refresh and self._cached is not None:
            age = time.time() - self._cached.fetched_at
            if age < self._ttl:
                return self._cached

        try:
            schema = await self._fetch_schema(include_sample_rows=include_sample_rows)
        except Exception as exc:
            logger.exception("schema.inspect.failed")
            raise SchemaInspectionError(f"Neuspjeh pri dohvatu sheme: {exc}") from exc

        if not include_sample_rows:
            self._cached = schema
        logger.info("schema.fetched", table_count=len(schema.tables), samples=include_sample_rows)
        return schema

    async def _fetch_schema(self, include_sample_rows: bool = False) -> DatabaseSchema:
        async with self._engine.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: _extract_tables(sync_conn, include_sample_rows=include_sample_rows))

        return DatabaseSchema(tables=tuple(tables))


SAMPLE_ROW_LIMIT = 5
MAX_CATEGORICAL_VALUES = 30


def _extract_tables(sync_conn, include_sample_rows: bool = False) -> list[TableInfo]:
    inspector: Inspector = inspect(sync_conn)
    result: list[TableInfo] = []

    dialect_name = sync_conn.dialect.name
    schema_name: str | None = "public" if dialect_name == "postgresql" else None

    for table_name in inspector.get_table_names(schema=schema_name):
        pk_cols = set(inspector.get_pk_constraint(table_name, schema=schema_name).get("constrained_columns") or [])

        # First pass: build columns with empty categorical_values.
        raw_columns = list(inspector.get_columns(table_name, schema=schema_name))
        columns: list[ColumnInfo] = []
        for col in raw_columns:
            categorical: tuple[str, ...] = ()
            if include_sample_rows and _looks_like_text_column(str(col["type"])):
                categorical = _fetch_distinct_values(
                    sync_conn, table_name, col["name"]
                )
            columns.append(
                ColumnInfo(
                    name=col["name"],
                    data_type=str(col["type"]),
                    nullable=bool(col.get("nullable", True)),
                    is_primary_key=col["name"] in pk_cols,
                    categorical_values=categorical,
                )
            )

        fks: list[ForeignKeyInfo] = []
        for fk in inspector.get_foreign_keys(table_name, schema=schema_name):
            fks.append(
                ForeignKeyInfo(
                    constrained_columns=tuple(fk.get("constrained_columns") or ()),
                    referred_table=str(fk.get("referred_table") or ""),
                    referred_columns=tuple(fk.get("referred_columns") or ()),
                )
            )

        sample_rows: tuple[tuple[Any, ...], ...] = ()
        if include_sample_rows:
            sample_rows = _fetch_sample_rows(sync_conn, table_name, pk_cols)

        result.append(
            TableInfo(
                name=table_name,
                columns=tuple(columns),
                foreign_keys=tuple(fks),
                sample_rows=sample_rows,
            )
        )

    return result


def _looks_like_text_column(data_type: str) -> bool:
    upper = data_type.upper()
    return any(token in upper for token in ("CHAR", "TEXT", "VARCHAR", "STRING"))


def _fetch_distinct_values(
    sync_conn, table_name: str, column_name: str
) -> tuple[str, ...]:
    qt = f'"{table_name}"'
    qc = f'"{column_name}"'
    try:
        rows = sync_conn.execute(
            text(f"SELECT DISTINCT {qc} FROM {qt} WHERE {qc} IS NOT NULL LIMIT :n"),
            {"n": MAX_CATEGORICAL_VALUES + 1},
        ).fetchall()
    except Exception:
        return ()
    if len(rows) > MAX_CATEGORICAL_VALUES:
        return ()
    return tuple(str(r[0]) for r in rows)


def _fetch_sample_rows(sync_conn, table_name: str, pk_cols: set[str]) -> tuple[tuple[Any, ...], ...]:
    qt = f'"{table_name}"'
    order_clause = ""
    if pk_cols:
        first_pk = sorted(pk_cols)[0]
        order_clause = f' ORDER BY "{first_pk}" DESC'
    try:
        rows = sync_conn.execute(
            text(f"SELECT * FROM {qt}{order_clause} LIMIT {SAMPLE_ROW_LIMIT}")
        ).fetchall()
    except Exception:  # noqa: BLE001 — fall back to unordered sample
        try:
            rows = sync_conn.execute(
                text(f"SELECT * FROM {qt} LIMIT {SAMPLE_ROW_LIMIT}")
            ).fetchall()
        except Exception:
            return ()
    return tuple(tuple(_truncate_cell(cell) for cell in r) for r in rows)


def _truncate_cell(value: Any, max_chars: int = 60) -> Any:
    if isinstance(value, str) and len(value) > max_chars:
        return value[: max_chars - 1] + "…"
    return value
