"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import databases, evaluate, health, providers, query, schema
from app.config import APP_VERSION
from app.core.exceptions import NL2SQLError
from app.core.logging import configure_logging, get_logger
from app.db.engine import dispose_engines

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("app.startup", version=APP_VERSION)
    try:
        yield
    finally:
        await dispose_engines()
        logger.info("app.shutdown")


app = FastAPI(
    title="NL2SQL",
    description="Sustav za generiranje SQL upita iz prirodnog jezika (diplomski rad).",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NL2SQLError)
async def domain_exception_handler(request: Request, exc: NL2SQLError) -> JSONResponse:
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

app.include_router(health.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(databases.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(evaluate.router, prefix="/api")
