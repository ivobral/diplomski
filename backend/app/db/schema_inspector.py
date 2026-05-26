"""Dinamički dohvat sheme baze.

Sustav NIKAD ne smije imati hardcoded shemu — pitanje korisnika u Fazi 2
prevodi se u SQL koji koristi tablice i kolone iz STVARNE baze. Zato ovaj
modul nudi:

- popis tablica (s primary key-evima),
- popis kolona po tablici (s tipovima),
- relacije (foreign keys) između tablica.

Implementacijska bilješka: SQLAlchemy ``inspect()`` API radi nad sync
engine-om, pa ga pozivamo unutar ``run_sync()`` mosta koji ga izvršava
u kontekstu async engine-a. Rezultat keširamo s TTL-om jer schema introspection
nije besplatna i shema se rijetko mijenja tijekom rada aplikacije.
"""

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


# ----------------------------------------------------------------------
# Data classes — namjerno koristimo dataclass umjesto Pydantic-a ovdje,
# jer ovo su interni modeli (ne idu direktno preko API-ja). Pydantic DTO-i
# za API leže u app/models/schema.py.
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """Jedna kolona u tablici."""

    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool


@dataclass(frozen=True, slots=True)
class ForeignKeyInfo:
    """Foreign key veza — koji stupci ove tablice referenciraju koje stupce druge."""

    constrained_columns: tuple[str, ...]
    referred_table: str
    referred_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableInfo:
    """Cjelovita informacija o jednoj tablici."""

    name: str
    columns: tuple[ColumnInfo, ...]
    foreign_keys: tuple[ForeignKeyInfo, ...]
    # Sample redovi za prompt context — opcionalni. Prazan tuple = nije fetched.
    # Pomaže LLM-u s case-sensitive value matching, raspodjelom NULL-ova,
    # primjenama formatima datuma itd. Tipično 3 retka po tablici.
    sample_rows: tuple[tuple[Any, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class DatabaseSchema:
    """Snapshot cijele sheme baze u jednoj točki u vremenu."""

    tables: tuple[TableInfo, ...]
    fetched_at: float = field(default_factory=time.time)

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def find_table(self, name: str) -> TableInfo | None:
        """Lookup po imenu, case-insensitive (PostgreSQL identifikatori su lowercase)."""

        lower = name.lower()
        for t in self.tables:
            if t.name.lower() == lower:
                return t
        return None


# ----------------------------------------------------------------------
# Schema Inspector
# ----------------------------------------------------------------------


class SchemaInspector:
    """Dohvaća i keširi shemu baze.

    Cache je in-memory s TTL-om iz ``settings.SCHEMA_CACHE_TTL_SECONDS``.
    Razlog: schema introspection radi nekoliko round-tripova prema bazi,
    a shema se rijetko mijenja — keširanje smanjuje latenciju pojedinih
    upita za 50-200ms (mjereno empirijski).
    """

    def __init__(self, engine: AsyncEngine, cache_ttl_seconds: int | None = None) -> None:
        self._engine = engine
        self._ttl = cache_ttl_seconds if cache_ttl_seconds is not None else settings.SCHEMA_CACHE_TTL_SECONDS
        self._cached: DatabaseSchema | None = None

    async def get_schema(
        self,
        force_refresh: bool = False,
        include_sample_rows: bool = False,
    ) -> DatabaseSchema:
        """Vraća trenutnu shemu, koristeći cache ako je svjež.

        Args:
            force_refresh: Ignoriraj cache i dohvati shemu iznova.
            include_sample_rows: ako True, fetch 3 sample retka po tablici.
                Korisno u benchmark mode-u da LLM može vidjeti stvarne
                vrijednosti (case sensitivity, format datuma, distribucija
                NULL-ova). Default False radi cijene cache-a.

        Raises:
            SchemaInspectionError: Ako introspection ne uspije.
        """

        # Cache nikad ne vraća verziju sa sample rows jer su one velike i
        # mogu se mijenjati. Sample-row varijantu uvijek freshly dohvaćamo.
        if not include_sample_rows and not force_refresh and self._cached is not None:
            age = time.time() - self._cached.fetched_at
            if age < self._ttl:
                return self._cached

        try:
            schema = await self._fetch_schema(include_sample_rows=include_sample_rows)
        except Exception as exc:  # noqa: BLE001 — wrap u domain exception
            logger.exception("schema.inspect.failed")
            raise SchemaInspectionError(f"Neuspjeh pri dohvatu sheme: {exc}") from exc

        # Cache-iramo samo verziju BEZ sample rows-a (manja, dijeli se
        # između demo i ostalih scenarija). Verziju s sample-om dohvaćamo
        # fresh svaki put — pretpostavlja se da je benchmark per-DB.
        if not include_sample_rows:
            self._cached = schema
        logger.info("schema.fetched", table_count=len(schema.tables), samples=include_sample_rows)
        return schema

    async def _fetch_schema(self, include_sample_rows: bool = False) -> DatabaseSchema:
        """Stvarni introspection — radi se sync API SQLAlchemy-ja preko run_sync mosta."""

        # SQLAlchemy ``inspect()`` je sinkroni API; async engine pruža `connect()`
        # i `run_sync(fn)` pomoću kojeg ga zovemo unutar event loop-a, bez
        # blokiranja drugih corutine-a.
        async with self._engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: _extract_tables(sync_conn, include_sample_rows=include_sample_rows)
            )

        return DatabaseSchema(tables=tuple(tables))


def _extract_tables(sync_conn, include_sample_rows: bool = False) -> list[TableInfo]:
    """Sinkrona funkcija koja se izvršava unutar run_sync — ekstrahira sve tablice.

    Razdvojena je iz ``SchemaInspector`` klase jer ``run_sync`` očekuje
    plain callable. Drži se na razini modula da je čitljivo i testabilno.

    Ako ``include_sample_rows=True``, fetcha SELECT * FROM <t> LIMIT 3 za
    svaku tablicu da LLM dobiva pravi pogled na podatke (case sensitivity,
    format vrijednosti, NULL distribucija).
    """

    inspector: Inspector = inspect(sync_conn)
    result: list[TableInfo] = []

    # Default schema u PostgreSQL-u je `public`; eksplicitno je tražimo da
    # izbjegnemo `pg_catalog` i `information_schema` interne tablice.
    # SQLite, suprotno, NEMA schema concept — ako pošaljemo `schema="public"`
    # SQLAlchemy pokuša `public.sqlite_master` što ne postoji. Detect dialect
    # iz konekcije i pass `None` za SQLite (znači "default schema").
    dialect_name = sync_conn.dialect.name  # 'postgresql', 'sqlite', 'mysql', …
    schema_name: str | None = "public" if dialect_name == "postgresql" else None

    for table_name in inspector.get_table_names(schema=schema_name):
        pk_cols = set(inspector.get_pk_constraint(table_name, schema=schema_name).get("constrained_columns") or [])

        columns: list[ColumnInfo] = []
        for col in inspector.get_columns(table_name, schema=schema_name):
            columns.append(
                ColumnInfo(
                    name=col["name"],
                    data_type=str(col["type"]),
                    nullable=bool(col.get("nullable", True)),
                    is_primary_key=col["name"] in pk_cols,
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
            # Quoted identifier — neka BIRD imena tablica imaju razmake
            # ili neobične znakove. SQLite koristi "" (kao i Postgres).
            quoted = f'"{table_name}"'
            try:
                rows = sync_conn.execute(text(f"SELECT * FROM {quoted} LIMIT 3")).fetchall()
                sample_rows = tuple(
                    tuple(_truncate_cell(cell) for cell in r) for r in rows
                )
            except Exception:
                # Tihi fallback: ako sample fetch padne (npr. permission),
                # ne rušimo cijeli introspection — samo izostavimo samples.
                sample_rows = ()

        result.append(
            TableInfo(
                name=table_name,
                columns=tuple(columns),
                foreign_keys=tuple(fks),
                sample_rows=sample_rows,
            )
        )

    return result


def _truncate_cell(value: Any, max_chars: int = 60) -> Any:
    """Obreže dugačke string vrijednosti da prompt ne ekspolodira.

    Ostavlja non-string vrijednosti (int/float/None) netaknute.
    """

    if isinstance(value, str) and len(value) > max_chars:
        return value[: max_chars - 1] + "…"
    return value
