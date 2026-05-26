"""Izvršavanje validiranog SQL-a — uvijek kroz read-only engine.

Pravila ovog sloja (NE smiju se zaobići):
1. SQL koji ulazi MORA biti normalizirani izlaz validatora (ne raw LLM
   output). QueryService garantira ovaj poredak; ako se ikad prebrza,
   read-only DB user je posljednja crta obrane.
2. Svaki upit ima timeout — sprječava DoS kroz dugotrajne upite.
3. Vraćamo prvih N redova kao listu lista (JSON-friendly) plus ime kolona.

Ako u Fazi 4 ili kasnije zatreba pagination ili streaming, dodaje se ovdje
— bez utjecaja na ostatak pipeline-a.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.exceptions import ExecutionError
from app.core.logging import get_logger
from app.core.timing import Timer

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Rezultat izvršavanja jednog SQL upita."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    execution_ms: float


class QueryExecutor:
    """Sigurno izvršava SELECT SQL kroz read-only engine."""

    def __init__(self, readonly_engine: AsyncEngine, timeout_seconds: int) -> None:
        self._engine = readonly_engine
        self._timeout = timeout_seconds

    async def execute(self, sql: str) -> ExecutionResult:
        """Izvrši validirani SQL i vrati strukturirani rezultat.

        Args:
            sql: validirani, normalizirani SQL string (iz validatora).

        Returns:
            ExecutionResult s kolonama, redovima i mjerenom latencijom.

        Raises:
            ExecutionError: pri timeout-u ili DB greški. Pozivni sloj
                (QueryService) ovu iznimku pretvara u user-friendly response.
        """

        with Timer() as t:
            try:
                # asyncio.wait_for omotava izvršavanje s timeout-om — ako
                # upit traje duže od `timeout_seconds`, baca TimeoutError
                # i konekcija se zatvara. To je naša obrana protiv DoS-a
                # i kompleksnih CROSS JOIN upita.
                async with self._engine.connect() as conn:
                    result_proxy = await asyncio.wait_for(
                        conn.execute(text(sql)),
                        timeout=self._timeout,
                    )
                    # `keys()` daje imena kolona, `fetchall()` sve redove.
                    columns = list(result_proxy.keys())
                    rows = [list(r) for r in result_proxy.fetchall()]
            except TimeoutError as exc:
                logger.warning("execution.timeout", timeout=self._timeout)
                raise ExecutionError(
                    f"SQL izvršavanje prekoračilo timeout ({self._timeout}s)."
                ) from exc
            except SQLAlchemyError as exc:
                logger.exception("execution.db.error")
                # SQLAlchemy iznimke nose i niži-razinske detalje (npr.
                # asyncpg poruke). Vraćamo skraćenu poruku korisniku —
                # puni stack trace već je u logu.
                raise ExecutionError(f"Greška izvršavanja: {exc.__class__.__name__}") from exc

        result = ExecutionResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_ms=t.elapsed_ms,
        )
        logger.info("execution.ok", rows=result.row_count, ms=result.execution_ms)
        return result
