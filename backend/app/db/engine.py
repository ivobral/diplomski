"""SQLAlchemy async engine — singleton po konekciji.

Aplikacija drži dva engine-a:
- ``main`` engine — koristi se za schema inspection i administrativne radnje
  (full read pristup, ali NIKAD za izvršavanje LLM-generiranog SQL-a).
- ``readonly`` engine — koristi se isključivo za izvršavanje LLM-generiranog
  SQL-a. Spojen je s korisnikom koji ima samo ``GRANT SELECT``.

Razdvajanje engine-a je sloj sigurnosti — čak i kad bi validacija pala
(što ne smije!), DB user ne može izvršiti DDL/DML. Ovo je defense-in-depth.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings

# Modul-level cache — engine-i su skupi za stvaranje (otvaraju connection
# pool), pa ih kreiramo jednom i recikliramo. None znači "još nije inicijaliziran".
_main_engine: AsyncEngine | None = None
_readonly_engine: AsyncEngine | None = None


def get_main_engine() -> AsyncEngine:
    """Vraća (lazy-inicijalizirani) glavni async engine.

    Glavni engine se koristi za dohvat sheme i administrativne radnje;
    NIKAD ne izvršava LLM-generirani SQL — za to služi readonly engine.
    """

    global _main_engine
    if _main_engine is None:
        _main_engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
        )
    return _main_engine


def get_readonly_engine() -> AsyncEngine:
    """Vraća async engine vezan na read-only DB usera.

    Svaki SQL koji generira LLM mora se izvršavati isključivo kroz ovaj
    engine — to je posljednja linija obrane ako bi validacija pala.
    """

    global _readonly_engine
    if _readonly_engine is None:
        _readonly_engine = create_async_engine(
            settings.READONLY_DATABASE_URL,
            echo=False,
            pool_pre_ping=True,
        )
    return _readonly_engine


async def dispose_engines() -> None:
    """Zatvara connection poolove (poziva se pri shutdown-u FastAPI app-a)."""

    global _main_engine, _readonly_engine
    if _main_engine is not None:
        await _main_engine.dispose()
        _main_engine = None
    if _readonly_engine is not None:
        await _readonly_engine.dispose()
        _readonly_engine = None
