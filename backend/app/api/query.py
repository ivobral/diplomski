"""POST /api/query — generiranje i izvršavanje SQL-a iz prirodnog jezika.

Tanki handler koji delegira sav rad u ``QueryService``. Sva logika tijeka
(LLM → validacija → retry → izvršavanje) živi u service sloju; ovdje samo
prevodimo HTTP request u service poziv i vraćamo response.

Provider override (``request.provider``) omogućava frontend dropdown za
usporedbu modela bez restart-a backenda. Ako nije naveden, koristi se
default iz ``settings.LLM_PROVIDER``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_llm_provider_for, get_query_service
from app.llm.base import BaseLLMProvider
from app.models.query import QueryRequest, QueryResponse
from app.services.query_service import QueryService

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    service: QueryService = Depends(get_query_service),
) -> QueryResponse:
    """Pretvori prirodno-jezično pitanje u SQL, validiraj i izvrši."""

    # Provider override se rješava ovdje (handler sloj), a ne unutar
    # QueryService-a, da Service ostane čist od HTTP/DI concerns.
    # ``get_llm_provider_for`` je @lru_cache-iran po imenu — više requestova
    # za isti provider dijele istu instancu.
    provider_override: BaseLLMProvider | None = None
    if request.provider is not None:
        provider_override = get_llm_provider_for(request.provider)

    return await service.execute_query(
        question=request.question,
        strategy_name=request.strategy,
        provider_override=provider_override,
    )
