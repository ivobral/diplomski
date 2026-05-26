"""FastAPI dependency providers.

Sve ovisnosti koje routeri trebaju (SchemaInspector, LLM provider, validator,
itd.) iniciraju se ovdje i ubrizgavaju kroz ``Depends()``. Pristup:

- ``@lru_cache(maxsize=1)`` — singleton po procesu (sve stateless servise
  želimo dijeliti).
- Konstrukcija je lazy — prvi request pokreće instanciranje, ne startup.
  Razlog: ako LLM ključ nedostaje, želimo pasti tek kad netko stvarno
  napravi query, a ne odmah pri pokretanju (omogućuje /api/schema da
  radi i bez konfiguriranog LLM-a).

Sve dependency funkcije su ``def`` (ne ``async def``) — jer same nemaju
I/O, samo wiring. FastAPI prihvaća oba oblika.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.db.engine import get_main_engine, get_readonly_engine
from app.db.schema_inspector import SchemaInspector
from app.llm.base import BaseLLMProvider
from app.llm.factory import create_llm_provider, create_llm_provider_for
from app.llm.prompts.builder import PromptBuilder
from app.services.execution_service import QueryExecutor
from app.services.query_service import QueryService
from app.services.retry_engine import RetryEngine
from app.validation.validator import SqlValidator

# ----------------------------------------------------------------------
# Stateless singletoni — kreiraju se jednom po procesu.
# ----------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_schema_inspector() -> SchemaInspector:
    """SchemaInspector ima vlastiti TTL cache; jedna instanca po procesu."""

    return SchemaInspector(engine=get_main_engine())


@lru_cache(maxsize=1)
def get_llm_provider() -> BaseLLMProvider:
    """Aktivni LLM provider odabran kroz ``settings.LLM_PROVIDER``.

    Lazy — pada s ``ConfigurationError`` tek na prvom pozivu ako ključ/model
    nedostaje. To je namjerno: /api/health i /api/schema rade bez LLM-a.
    """

    return create_llm_provider()


@lru_cache(maxsize=8)
def get_llm_provider_for(name: str) -> BaseLLMProvider:
    """Vraća (cached) provider instancu za eksplicitno ime.

    Koristi se kad request specificira ``provider`` field — frontend dropdown
    za usporedbu modela. Cache po imenu omogućava brze re-pozive bez novog
    SDK setup-a; maxsize=8 je daleko više od broja podržanih providera.
    """

    return create_llm_provider_for(name)


@lru_cache(maxsize=1)
def get_prompt_builder() -> PromptBuilder:
    return PromptBuilder(schema_inspector=get_schema_inspector())


@lru_cache(maxsize=1)
def get_validator() -> SqlValidator:
    return SqlValidator(
        schema_inspector=get_schema_inspector(),
        default_limit=settings.DEFAULT_LIMIT,
    )


@lru_cache(maxsize=1)
def get_query_executor() -> QueryExecutor:
    """Executor uvijek koristi readonly engine — DEFENSE IN DEPTH."""

    return QueryExecutor(
        readonly_engine=get_readonly_engine(),
        timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
    )


@lru_cache(maxsize=1)
def get_retry_engine() -> RetryEngine:
    return RetryEngine(
        provider=get_llm_provider(),
        prompt_builder=get_prompt_builder(),
        max_attempts=settings.MAX_RETRY_ATTEMPTS,
    )


@lru_cache(maxsize=1)
def get_query_service() -> QueryService:
    """Glavna service-layer ovisnost koju koristi POST /api/query."""

    return QueryService(
        schema_inspector=get_schema_inspector(),
        prompt_builder=get_prompt_builder(),
        provider=get_llm_provider(),
        validator=get_validator(),
        executor=get_query_executor(),
        retry_engine=get_retry_engine(),
    )
