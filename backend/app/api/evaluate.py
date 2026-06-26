"""POST /api/evaluate — placeholder za benchmark preko HTTP-a.

Benchmark se trenutno pokreće preko CLI (``scripts/run_benchmark_cli.py``),
ne preko HTTP-a — runovi traju 10-30 minuta što je predugačko za sinkroni
request. HTTP endpoint bi zahtijevao job queue + async polling, što je
overkill za diplomski demo.

Endpoint vraća 501 Not Implemented da bude jasno u OpenAPI dokumentaciji.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter(prefix="/evaluate", tags=["evaluation"])


@router.post("")
async def evaluate() -> dict:
    """Vraća 501. Koristi CLI: ``python scripts/run_benchmark_cli.py``."""

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Benchmark se pokreće preko CLI-a, ne HTTP-a. "
            "Vidi: scripts/run_benchmark_cli.py (10-30 min trajanje)."
        ),
    )
