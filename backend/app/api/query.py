"""POST /api/query — generate and execute SQL from a natural-language question.

Dispatches to one of two services based on ``request.database``:

- ``"chinook"`` (default) → ``QueryService`` (PostgreSQL demo).
- BIRD database id     → ``BenchmarkQueryService`` (SQLite, full D pipeline).

The handler converts the BIRD result (``BenchmarkQuestionResult``) to the
public ``QueryResponse`` shape so the frontend renders both paths the same.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import (
    get_benchmark_query_service,
    get_bird_loader,
    get_llm_provider,
    get_llm_provider_for,
    get_query_service,
)
from app.evaluation.bird_loader import BirdLoader, BirdQuestion
from app.llm.base import BaseLLMProvider
from app.models.query import LatencyBreakdown, QueryRequest, QueryResponse
from app.services.benchmark_query_service import (
    BenchmarkQueryService,
    BenchmarkQuestionResult,
)
from app.services.query_service import QueryService

router = APIRouter(prefix="/query", tags=["query"])


@router.post("", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    chinook_service: QueryService = Depends(get_query_service),
    bench_service: BenchmarkQueryService = Depends(get_benchmark_query_service),
    bird_loader: BirdLoader = Depends(get_bird_loader),
    default_provider: BaseLLMProvider = Depends(get_llm_provider),
) -> QueryResponse:
    """Convert a natural-language question into SQL, validate, and execute."""

    # Provider override is resolved here (HTTP layer), not inside services,
    # so the services remain clean of DI concerns. ``get_llm_provider_for``
    # is @lru_cache'd by name.
    provider: BaseLLMProvider = (
        get_llm_provider_for(request.provider)
        if request.provider is not None
        else default_provider
    )

    # Chinook = the original PostgreSQL demo path.
    if request.database == "chinook":
        return await chinook_service.execute_query(
            question=request.question,
            strategy_name=request.strategy,
            provider_override=(
                provider if request.provider is not None else None
            ),
        )

    # BIRD path — validate the db_id, then run through BenchmarkQueryService.
    if not bird_loader.is_ready():
        raise HTTPException(
            status_code=404,
            detail=(
                "BIRD dataset is not available. Run "
                "`docker compose exec backend python /app/scripts/download_bird.py`."
            ),
        )
    if request.database not in bird_loader.list_databases():
        raise HTTPException(
            status_code=404,
            detail=f"Unknown database: '{request.database}'",
        )

    # Wrap the ad-hoc UI question as a BirdQuestion. We don't have a gold
    # SQL (this is a live question, not benchmark eval), so gold_sql stays
    # empty and the result is presented without EX comparison.
    bird_q = BirdQuestion(
        question_id=-1,
        db_id=request.database,
        question=request.question,
        evidence="",
        gold_sql="",
        difficulty="unknown",
    )
    bench_result = await bench_service.evaluate(
        question=bird_q,
        strategy_code=request.strategy or "D",
        provider=provider,
    )
    return _benchmark_to_query_response(bench_result, request.question)


def _benchmark_to_query_response(
    result: BenchmarkQuestionResult,
    original_question: str,
) -> QueryResponse:
    """Adapt a benchmark result into the public ``QueryResponse`` shape.

    The fields don't map 1-to-1 — benchmark carries gold/difficulty metadata
    we don't expose to the UI, and ``QueryResponse`` has its own
    ``LatencyBreakdown`` structure. This adapter centralises the mapping.
    """

    return QueryResponse(
        question=original_question,
        generated_sql=result.predicted_sql,
        normalized_sql=result.normalized_sql,
        validated=result.validated,
        executed=result.executed,
        # In benchmark mode the validator's ``blocked_reason`` is folded into
        # ``error_reason``. We extract it back so the UI can colour the
        # status badge as "security block" vs "execution error".
        blocked_reason=result.error_reason if result.blocked else None,
        error=result.error_reason if (not result.blocked and result.error_reason) else None,
        columns=result.predicted_columns,
        rows=result.predicted_rows,
        row_count=len(result.predicted_rows),
        latency=LatencyBreakdown(
            llm_ms=result.llm_ms,
            validation_ms=result.validation_ms,
            execution_ms=result.execution_ms,
            total_ms=result.total_ms,
        ),
        retry_count=result.retry_count,
    )
