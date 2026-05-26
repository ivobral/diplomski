"""FastAPI application entry point.

Wire-up sloj — sastavlja konfiguraciju, logging, exception handlere i
registrira sve API routere. Ovo je jedino mjesto gdje se FastAPI ``app``
instanca kreira; ostali moduli je ne uvoze (cikličke ovisnosti).

Pokretanje: ``uvicorn app.main:app --host 0.0.0.0 --port 8000``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import evaluate, health, providers, query, schema
from app.core.exceptions import NL2SQLError
from app.core.logging import configure_logging, get_logger
from app.db.engine import dispose_engines

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup i shutdown hooks aplikacije.

    Pri startup-u: konfiguriramo logging. Pri shutdown-u: zatvaramo DB
    connection poolove da ne ostanu visećih konekcija.
    """

    configure_logging()
    logger.info("app.startup", version=__version__)
    try:
        yield
    finally:
        await dispose_engines()
        logger.info("app.shutdown")


app = FastAPI(
    title="NL2SQL",
    description="Sustav za generiranje SQL upita iz prirodnog jezika (diplomski rad).",
    version=__version__,
    lifespan=lifespan,
)

# CORS — frontend pokreće na localhost:3000 i zove backend na localhost:8000.
# Za diplomski demo dovoljan je permissive setup; za produkciju bi se ograničilo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NL2SQLError)
async def domain_exception_handler(request: Request, exc: NL2SQLError) -> JSONResponse:
    """Mapira domain iznimke u jasne HTTP responsove.

    Cilj: korisnik dobije razumljivu poruku, a logovi zadrže pun stack trace.
    """

    logger.warning(
        "domain.error",
        type=type(exc).__name__,
        path=request.url.path,
        detail=str(exc),
    )
    return JSONResponse(
        status_code=400,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


# Sva ruta žive pod /api prefiksom — frontend zove npr. POST /api/query.
# Razdvojeni routeri olakšavaju navigaciju i testabilnost.
app.include_router(health.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(evaluate.router, prefix="/api")
