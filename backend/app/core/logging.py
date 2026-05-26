"""Strukturirano logiranje preko structlog-a.

Logiranje je ključno za diplomski rad jer evaluacija mjeri latencije po
fazama (LLM ms / validation ms / execution ms) i analizira tijek pojedinih
upita. Strukturirani logovi (key-value parovi) puno su lakši za parsiranje
i analizu od običnih log poruka.

Dva formata izlaza:
- ``console`` — čitljiv za razvojni tijek (boje, formatiranje).
- ``json``    — strukturiran za produkciju i parsiranje skriptama.

Korištenje:

    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("query.received", question="...", provider="anthropic")
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.config import settings


def configure_logging() -> None:
    """Postavlja strukturirano logiranje za cijelu aplikaciju.

    Funkcija se poziva jednom pri startup-u (iz ``app.main``). Nakon poziva,
    svaki ``get_logger()`` vraća konfiguriran structlog logger.
    """

    # Standardni Python logging je osnova — structlog se mota oko njega.
    # Postavljamo razinu i jednostavan handler na stdout (Docker tako želi).
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.LOG_LEVEL,
    )

    # Processor pipeline — svaki log event prolazi kroz lanac. Posljednji
    # processor renderira finalni output (JSON ili pretty console).
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.LOG_FORMAT == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.LOG_LEVEL)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Vraća structlog logger vezan na zadano ime (obično modul).

    Args:
        name: Ime loggera, najčešće ``__name__`` modula koji loga.

    Returns:
        Strukturirani logger spreman za korištenje (`.info`, `.warning`, …).
    """

    return structlog.get_logger(name)
