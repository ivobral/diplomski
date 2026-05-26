"""SQLite executor za benchmark — izvršava SQL nad BIRD SQLite bazama.

Razdvojen od ``QueryExecutor`` (Chinook/PostgreSQL) namjerno: glavni demo
tijek ostaje netaknut, benchmark tijek koristi vlastiti executor.

Svaka BIRD baza je zaseban SQLite file:
    /app/data/bird_mini/databases/<db_id>/<db_id>.sqlite

Engine cache po ``db_id`` — sqlalchemy async engine pool drži konekcije
otvorene za vrijeme run-a (typično 100-200 pitanja preko ~10 različitih baza).

Sigurnost na razini SQLite-a: koristimo URI s ``mode=ro`` da konekcija ne
može pisati u file, čak i kad bi neki bug u kodu nekako proslijedio
ne-SELECT statement. Defense-in-depth (slično ``nl2sql_readonly`` PostgreSQL
useru za Chinook).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.exceptions import ExecutionError
from app.core.logging import get_logger
from app.core.timing import Timer
from app.services.execution_service import ExecutionResult

logger = get_logger(__name__)


class BenchmarkExecutor:
    """Izvršava SELECT-only SQL nad BIRD SQLite bazama."""

    def __init__(self, dataset_path: Path, timeout_seconds: int) -> None:
        """
        Args:
            dataset_path: korijenski folder BIRD dataset-a (sadrži ``databases/``).
            timeout_seconds: maksimalno vrijeme po upitu (DoS zaštita).
        """

        self._dataset_path = dataset_path
        self._timeout = timeout_seconds
        # Explicit dict za tracking engines — ne lru_cache. Razlog: lru_cache
        # exposuje ``cache_clear()`` ali NE iteraciju po vrijednostima, pa nije
        # moguće explicitno zatvoriti engine-e prilikom shutdown-a. To je
        # uzrokovalo hang nakon JSON pisanja (engine threadovi su držali
        # asyncio event loop živim satima nakon "logically done"-a).
        self._engines: dict[str, AsyncEngine] = {}

    def db_path(self, db_id: str) -> Path:
        """Vrati apsolutan put do SQLite file-a za zadanu BIRD bazu."""

        return self._dataset_path / "databases" / db_id / f"{db_id}.sqlite"

    def _get_engine(self, db_id: str) -> AsyncEngine:
        """Vraća (cached) async engine za zadani BIRD db_id.

        Engine je u read-only modu kroz SQLite URI parameter ``mode=ro``,
        što je sigurnosna garancija na razini drivera (kao GRANT SELECT u Postgres-u).

        Cached u ``self._engines`` (dict) umjesto ``@lru_cache`` da bismo
        mogli explicitly close-ati sve engine-e u ``dispose()``.
        """

        if db_id in self._engines:
            return self._engines[db_id]

        path = self.db_path(db_id)
        if not path.exists():
            raise ExecutionError(
                f"BIRD baza '{db_id}' nije pronađena na {path}. "
                f"Pokreni `scripts/download_bird.py` prvo."
            )
        # SQLite URI mode=ro — driver odbija sve write operacije.
        # `uri=true` aktivira parsing URI-ja (inače aiosqlite tretira string
        # kao običan file path i ignorira parametre).
        url = f"sqlite+aiosqlite:///file:{path}?mode=ro&uri=true"
        engine = create_async_engine(url, connect_args={"uri": True})
        self._engines[db_id] = engine
        return engine

    async def execute(self, sql: str, db_id: str) -> ExecutionResult:
        """Izvrši validirani SQL nad zadanom BIRD bazom.

        Args:
            sql: validirani SQL string (već prošao SqlValidator s dialect=sqlite).
            db_id: ime BIRD baze (npr. ``"california_schools"``).

        Returns:
            ExecutionResult s kolonama, redovima i mjerenom latencijom.

        Raises:
            ExecutionError: pri timeout-u, otsutnosti baze, ili SQL greški.
        """

        engine = self._get_engine(db_id)

        with Timer() as t:
            try:
                async with engine.connect() as conn:
                    result_proxy = await asyncio.wait_for(
                        conn.execute(text(sql)),
                        timeout=self._timeout,
                    )
                    columns = list(result_proxy.keys())
                    rows = [list(r) for r in result_proxy.fetchall()]
            except TimeoutError as exc:
                logger.warning("benchmark.execution.timeout", db_id=db_id, timeout=self._timeout)
                raise ExecutionError(
                    f"SQL izvršavanje prekoračilo timeout ({self._timeout}s) na {db_id}."
                ) from exc
            except SQLAlchemyError as exc:
                logger.warning("benchmark.execution.error", db_id=db_id, error=str(exc))
                raise ExecutionError(f"SQL greška na {db_id}: {exc.__class__.__name__}: {exc}") from exc

        result = ExecutionResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_ms=t.elapsed_ms,
        )
        logger.debug("benchmark.execution.ok", db_id=db_id, rows=result.row_count)
        return result

    async def dispose(self) -> None:
        """Zatvori sve cached engine-e (za clean shutdown nakon benchmark-a).

        VAŽNO: bez ovoga, asyncio event loop neće završiti čak i nakon što
        je benchmark JSON written. SQLAlchemy + aiosqlite drže background
        thread po engine-u koji čekaju event-e.

        Per-engine timeout (3s): ako neki engine ima zombie konekcije iz
        cancellation-a u gold execution-u (`asyncio.wait_for(timeout=300)`
        u runner-u koji prekine queries usred izvršavanja), njegov dispose
        bi mogao visiti zauvijek. Bolje preskočiti tu engine nego visiti.
        """

        for db_id, engine in self._engines.items():
            try:
                await asyncio.wait_for(engine.dispose(), timeout=3.0)
            except TimeoutError:
                logger.warning("benchmark.executor.dispose_timeout", db_id=db_id)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                logger.warning("benchmark.executor.dispose_failed", db_id=db_id, error=str(exc))
        self._engines.clear()
