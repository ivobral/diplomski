"""End-to-end orchestrator: pitanje → SQL → validacija → izvršavanje.

Ovaj servis je glavni "use case" backenda. API handler (``/api/query``)
je tanak wrapper koji ga zove; cijela logika tijeka rada je ovdje.

Tijek (s mjernim točkama):

    1. odaberi strategiju (default = D)
    2. PromptBuilder → Prompt
    3. LLMProvider → SQL                       [llm_ms]
    4. SqlValidator → ValidationResult         [validation_ms]
    5. ako fail (parse/semantic) → RetryEngine → 3-4, do max_attempts
       (safety-blocked NIKAD ne ide u retry)
    6. ako blocked → return s blocked_reason
    7. ako još uvijek invalid → return s error
    8. QueryExecutor → ExecutionResult         [execution_ms]
    9. return QueryResponse s svim podacima i latency breakdown-om
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.db.schema_inspector import SchemaInspector
from app.llm.base import BaseLLMProvider
from app.llm.prompts.builder import PromptBuilder
from app.llm.prompts.strategies import get_strategy
from app.models.query import LatencyBreakdown, QueryResponse
from app.models.validation import ValidationResult
from app.services.execution_service import QueryExecutor
from app.services.retry_engine import RetryEngine
from app.validation.validator import SqlValidator

logger = get_logger(__name__)


class QueryService:
    """Orchestrator koji povezuje sve komponente Faze 2."""

    def __init__(
        self,
        schema_inspector: SchemaInspector,
        prompt_builder: PromptBuilder,
        provider: BaseLLMProvider,
        validator: SqlValidator,
        executor: QueryExecutor,
        retry_engine: RetryEngine,
    ) -> None:
        self._inspector = schema_inspector
        self._prompt_builder = prompt_builder
        self._provider = provider
        self._validator = validator
        self._executor = executor
        self._retry_engine = retry_engine

    async def execute_query(
        self,
        question: str,
        strategy_name: str | None,
        provider_override: BaseLLMProvider | None = None,
    ) -> QueryResponse:
        """Pokreni cijeli tijek za jedno korisničko pitanje.

        Args:
            question: prirodno-jezično pitanje.
            strategy_name: "A"/"B"/"C"/"D" ili ``None`` (= D).
            provider_override: ako je naveden, koristi se umjesto default-a
                iz konstruktora. Omogućava frontend dropdown za usporedbu
                modela bez restart-a backenda. Retry koristi isti override.

        Returns:
            ``QueryResponse`` s rezultatom, statusom, latency breakdown-om
            i brojem retry-ova. Nikad ne baca iznimku za "očekivane"
            situacije (validation fail, blocked) — sve se odražava u DTO-u.
        """

        # Effective provider — koristi override ako je dat, inače default.
        # Drži se u lokalnoj varijabli tako da retry koristi *isti* provider
        # za korekciju (mijenjanje providera usred retry-a nema smisla i
        # zamutilo bi rezultate eksperimenta).
        active_provider = provider_override or self._provider

        strategy = get_strategy(strategy_name)
        logger.info(
            "query.received",
            question=question[:120],
            strategy=strategy.code,
            provider=active_provider.name(),
        )

        # LatencyBreakdown je mutable Pydantic model; akumuliramo vrijednosti
        # in-place kroz pipeline. Kumulativno = uključuje retry pokušaje.
        latency = LatencyBreakdown(llm_ms=0.0, validation_ms=0.0, execution_ms=0.0, total_ms=0.0)

        # ----- Inicijalni LLM poziv ------------------------------------
        prompt = await self._prompt_builder.build(question, strategy)
        llm_response = await active_provider.generate(prompt)
        latency.llm_ms = (latency.llm_ms or 0) + llm_response.latency_ms

        current_sql = llm_response.sql

        # ----- Validacija (+ eventualni retry petlja) ------------------
        # Strategija D je jedina koja koristi retry; A/B/C ostaju s prvim
        # validation rezultatom (eksperimentalna metodologija — Faza 4).
        validation = await self._timed_validate(current_sql, latency)
        retry_count = 0

        if strategy.code == "D":
            while (
                not validation.ok
                and validation.blocked_reason is None  # NIKAD retry safety-blocked
                and retry_count < self._retry_engine.max_attempts
            ):
                retry_count += 1
                llm_response, _ = await self._retry_engine.attempt_correction(
                    question=question,
                    previous_sql=current_sql,
                    validation_errors=validation.errors,
                    attempt_num=retry_count,
                    provider_override=active_provider,
                )
                latency.llm_ms = (latency.llm_ms or 0) + llm_response.latency_ms
                current_sql = llm_response.sql
                validation = await self._timed_validate(current_sql, latency)

        # ----- Završetak — odluka prema validation stanju --------------
        if validation.blocked_reason is not None:
            logger.warning("query.blocked", reason=validation.blocked_reason)
            return self._build_blocked_response(
                question=question,
                raw_sql=current_sql,
                validation=validation,
                latency=latency,
                retry_count=retry_count,
            )

        if not validation.ok:
            logger.warning("query.invalid", errors=validation.errors[:3])
            return self._build_error_response(
                question=question,
                raw_sql=current_sql,
                validation=validation,
                latency=latency,
                retry_count=retry_count,
            )

        # ----- Izvršavanje -------------------------------------------
        assert validation.normalized_sql is not None
        try:
            exec_result = await self._executor.execute(validation.normalized_sql)
        except Exception as exc:
            logger.exception("query.execution.failed")
            return QueryResponse(
                question=question,
                generated_sql=current_sql,
                normalized_sql=validation.normalized_sql,
                validated=True,
                executed=False,
                error=str(exc),
                latency=latency,
                retry_count=retry_count,
            )

        latency.execution_ms = exec_result.execution_ms
        latency.total_ms = (
            (latency.llm_ms or 0) + (latency.validation_ms or 0) + exec_result.execution_ms
        )

        logger.info(
            "query.completed",
            rows=exec_result.row_count,
            llm_ms=latency.llm_ms,
            validation_ms=latency.validation_ms,
            execution_ms=latency.execution_ms,
            total_ms=latency.total_ms,
            retries=retry_count,
        )

        return QueryResponse(
            question=question,
            generated_sql=current_sql,
            normalized_sql=validation.normalized_sql,
            validated=True,
            executed=True,
            columns=exec_result.columns,
            rows=exec_result.rows,
            row_count=exec_result.row_count,
            latency=latency,
            retry_count=retry_count,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _timed_validate(
        self, sql: str, latency: LatencyBreakdown
    ) -> ValidationResult:
        """Validacija s kumulativnim mjerenjem latencije."""

        from app.core.timing import Timer  # lokalni import da izbjegnemo circular

        with Timer() as t:
            result = await self._validator.validate(sql)
        # Kumulativna validation latencija (uključuje retry validacije).
        latency.validation_ms = (latency.validation_ms or 0) + t.elapsed_ms
        return result

    def _build_blocked_response(
        self,
        question: str,
        raw_sql: str,
        validation: ValidationResult,
        latency: LatencyBreakdown,
        retry_count: int,
    ) -> QueryResponse:
        latency.total_ms = (latency.llm_ms or 0) + (latency.validation_ms or 0)
        return QueryResponse(
            question=question,
            generated_sql=raw_sql,
            normalized_sql=None,
            validated=False,
            executed=False,
            blocked_reason=validation.blocked_reason,
            latency=latency,
            retry_count=retry_count,
        )

    def _build_error_response(
        self,
        question: str,
        raw_sql: str,
        validation: ValidationResult,
        latency: LatencyBreakdown,
        retry_count: int,
    ) -> QueryResponse:
        latency.total_ms = (latency.llm_ms or 0) + (latency.validation_ms or 0)
        return QueryResponse(
            question=question,
            generated_sql=raw_sql,
            normalized_sql=None,
            validated=False,
            executed=False,
            error="; ".join(validation.errors),
            latency=latency,
            retry_count=retry_count,
        )
