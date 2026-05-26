"""Pytest fixtures — shared između svih test sub-foldera.

Fixtures:
- ``chinook_schema`` — hard-coded DatabaseSchema (kompatibilan s Chinook bazom)
  bez ovisnosti o pravom Postgres-u. Validator + PromptBuilder testovi koriste.
- ``pg_validator`` — SqlValidator s Postgres dialect-om (production default)
- ``sqlite_validator`` — SqlValidator sa SQLite dialect-om (BIRD benchmark)
- ``mock_llm_provider`` — fake LLM koji vraća fiksne SQL-ove (za integration)

Filozofija: što više čistih unit testova s fixed fixtures, manje testova koji
zahtijevaju živi servis. To čini test suite brzom, deterministicom i
neovisnom o eksternim API-jima.
"""

from __future__ import annotations

import pytest

from app.db.schema_inspector import ColumnInfo, DatabaseSchema, ForeignKeyInfo, TableInfo
from app.validation.validator import SqlValidator

# ----------------------------------------------------------------------
# Schema fixtures — Chinook subset, hard-coded da test ne treba živu bazu
# ----------------------------------------------------------------------


def _make_chinook_schema() -> DatabaseSchema:
    """Konstruira reprezentativnu Chinook DatabaseSchema.

    Sadrži glavne tablice za pokrivanje SQL test slučajeva: artist, album,
    customer, invoice, invoice_line, track, playlist, playlist_track, genre,
    media_type, employee. Ne treba potpunu Chinook shemu — samo dovoljno da
    semantic validator ima što provjeriti.

    Kolone su minimalne ali pokrivaju različite tipove (INTEGER PK, TEXT,
    NUMERIC, INTEGER FK) tako da JOIN-evi i agregacije imaju smisla.
    """

    def col(name: str, dtype: str = "TEXT", pk: bool = False) -> ColumnInfo:
        return ColumnInfo(name=name, data_type=dtype, nullable=not pk, is_primary_key=pk)

    def fk(cols: str | tuple[str, ...], to_table: str, to_cols: str | tuple[str, ...]) -> ForeignKeyInfo:
        c = (cols,) if isinstance(cols, str) else tuple(cols)
        rc = (to_cols,) if isinstance(to_cols, str) else tuple(to_cols)
        return ForeignKeyInfo(constrained_columns=c, referred_table=to_table, referred_columns=rc)

    tables = (
        TableInfo(name="artist", columns=(
            col("artist_id", "INTEGER", pk=True),
            col("name", "TEXT"),
        ), foreign_keys=()),
        TableInfo(name="album", columns=(
            col("album_id", "INTEGER", pk=True),
            col("title", "TEXT"),
            col("artist_id", "INTEGER"),
        ), foreign_keys=(fk("artist_id", "artist", "artist_id"),)),
        TableInfo(name="employee", columns=(
            col("employee_id", "INTEGER", pk=True),
            col("last_name", "TEXT"),
            col("first_name", "TEXT"),
            col("hire_date", "TIMESTAMP"),
        ), foreign_keys=()),
        TableInfo(name="customer", columns=(
            col("customer_id", "INTEGER", pk=True),
            col("first_name", "TEXT"),
            col("last_name", "TEXT"),
            col("country", "TEXT"),
            col("support_rep_id", "INTEGER"),
        ), foreign_keys=(fk("support_rep_id", "employee", "employee_id"),)),
        TableInfo(name="invoice", columns=(
            col("invoice_id", "INTEGER", pk=True),
            col("customer_id", "INTEGER"),
            col("invoice_date", "TIMESTAMP"),
            col("total", "NUMERIC"),
        ), foreign_keys=(fk("customer_id", "customer", "customer_id"),)),
        TableInfo(name="invoice_line", columns=(
            col("invoice_line_id", "INTEGER", pk=True),
            col("invoice_id", "INTEGER"),
            col("track_id", "INTEGER"),
            col("unit_price", "NUMERIC"),
            col("quantity", "INTEGER"),
        ), foreign_keys=(
            fk("invoice_id", "invoice", "invoice_id"),
            fk("track_id", "track", "track_id"),
        )),
        TableInfo(name="track", columns=(
            col("track_id", "INTEGER", pk=True),
            col("name", "TEXT"),
            col("album_id", "INTEGER"),
            col("genre_id", "INTEGER"),
            col("media_type_id", "INTEGER"),
            col("milliseconds", "INTEGER"),
            col("unit_price", "NUMERIC"),
        ), foreign_keys=(
            fk("album_id", "album", "album_id"),
            fk("genre_id", "genre", "genre_id"),
            fk("media_type_id", "media_type", "media_type_id"),
        )),
        TableInfo(name="playlist", columns=(
            col("playlist_id", "INTEGER", pk=True),
            col("name", "TEXT"),
        ), foreign_keys=()),
        TableInfo(name="playlist_track", columns=(
            col("playlist_id", "INTEGER"),
            col("track_id", "INTEGER"),
        ), foreign_keys=(
            fk("playlist_id", "playlist", "playlist_id"),
            fk("track_id", "track", "track_id"),
        )),
        TableInfo(name="genre", columns=(
            col("genre_id", "INTEGER", pk=True),
            col("name", "TEXT"),
        ), foreign_keys=()),
        TableInfo(name="media_type", columns=(
            col("media_type_id", "INTEGER", pk=True),
            col("name", "TEXT"),
        ), foreign_keys=()),
    )
    return DatabaseSchema(tables=tables)


@pytest.fixture(scope="session")
def chinook_schema() -> DatabaseSchema:
    """Session-scoped — sve testove dijele isti schema objekt (immutable u praksi)."""

    return _make_chinook_schema()


class _StubInspector:
    """Lažan SchemaInspector koji vraća hard-coded schema bez DB-a.

    Validator očekuje ``schema_inspector.get_schema()`` interface;
    ovo zadovoljava ugovor za testove koji ne traže pravu introspekciju.
    """

    def __init__(self, schema: DatabaseSchema) -> None:
        self._schema = schema

    async def get_schema(self, force_refresh: bool = False, include_sample_rows: bool = False) -> DatabaseSchema:  # noqa: ARG002
        return self._schema


@pytest.fixture
def pg_validator(chinook_schema: DatabaseSchema) -> SqlValidator:
    """Validator s Postgres dialect-om — production default za Chinook demo."""

    return SqlValidator(
        schema_inspector=_StubInspector(chinook_schema),
        default_limit=1000,
        default_dialect="postgres",
    )


@pytest.fixture
def sqlite_validator(chinook_schema: DatabaseSchema) -> SqlValidator:
    """Validator s SQLite dialect-om — koristi se za BIRD benchmark.

    Schema je ista (Chinook) jer ovi testovi provjeravaju dialect ponašanje,
    ne BIRD baze. SQLite-specifični testovi koriste ``datetime('now')`` i sl.
    """

    return SqlValidator(
        schema_inspector=_StubInspector(chinook_schema),
        default_limit=1000,
        default_dialect="sqlite",
    )
