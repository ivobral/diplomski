"""BenchmarkRunner — pokreće set BIRD pitanja kroz odabrane provider+strategy parove.

Filozofija:
- Paralelizam preko `asyncio.Semaphore(concurrency)` — kontroliramo koliko
  istovremenih LLM zahtjeva ide. Razumno za API rate limit-e (Gemini 1500/day,
  GitHub Models 150/day/model). Default concurrency=3 je konzervativan.
- Idempotentno za isti dataset/strategy/provider: deterministički ordering po
  question_id, ali ne caching (svaki run je neovisan).
- Persistira **BenchmarkRun** kao JSON s svim per-question rezultatima + agregatima.

Ako je tijek prekinut (Ctrl+C, mreža), parcijalni rezultati su izgubljeni u
prvoj iteraciji. Mogućnost resume-anja je "možda kasnije" (out of scope V1).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.evaluation.bird_loader import BirdLoader, BirdQuestion
from app.evaluation.metrics import GroupAggregates, aggregates_to_dict, compute_metrics
from app.evaluation.security_suite import SecurityReport, run_security_suite
from app.llm.base import BaseLLMProvider
from app.llm.prompts.builder import PromptBuilder
from app.services.benchmark_query_service import BenchmarkQueryService, BenchmarkQuestionResult
from app.validation.validator import SqlValidator

logger = get_logger(__name__)


@dataclass(slots=True)
class BenchmarkRun:
    """Kompletan rezultat jednog benchmark run-a."""

    run_id: str
    started_at: str            # ISO timestamp
    finished_at: str | None
    config: dict[str, Any]     # provideri, strategije, limit, itd.
    question_results: list[BenchmarkQuestionResult] = field(default_factory=list)
    aggregates: dict[tuple[str, str], GroupAggregates] = field(default_factory=dict)
    security_report: SecurityReport | None = None


class BenchmarkRunner:
    """Orchestrator koji kombinira sve dijelove benchmark-a."""

    def __init__(
        self,
        bird_loader: BirdLoader,
        benchmark_service: BenchmarkQueryService,
        validator: SqlValidator,
        prompt_builder: PromptBuilder,
        concurrency: int = 3,
    ) -> None:
        self._loader = bird_loader
        self._service = benchmark_service
        self._validator = validator
        self._prompt_builder = prompt_builder
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run(
        self,
        providers: dict[str, BaseLLMProvider],   # name → instance
        strategy_codes: list[str],
        limit: int | None = None,
        difficulty: str | None = None,
        include_security: bool = True,
        security_provider_name: str | None = None,
    ) -> BenchmarkRun:
        """Pokreni benchmark s zadanim parametrima.

        Args:
            providers: mapping ime providera → instanca (kao iz factory-ja).
            strategy_codes: lista strategija za pokretanje (npr. ["A","B","C","D"]).
            limit: maksimalan broj BIRD pitanja (None = sve).
            difficulty: filter po BIRD difficulty (None = sve težine).
            include_security: pokreni li security suitu kao zaseban korak.
            security_provider_name: koji provider koristiti za NL pipeline
                security test (default: prvi iz `providers`).

        Returns:
            ``BenchmarkRun`` s svim rezultatima i metrikama.
        """

        run_id = _make_run_id()
        started_at = datetime.now(UTC).isoformat()
        config = {
            "providers": sorted(providers.keys()),
            "strategies": list(strategy_codes),
            "limit": limit,
            "difficulty": difficulty,
            "include_security": include_security,
        }

        logger.info("benchmark.run.start", run_id=run_id, **config)

        # 1. Učitaj BIRD pitanja
        questions = self._loader.load_questions(limit=limit, difficulty=difficulty)
        logger.info("benchmark.questions.loaded", count=len(questions))

        # 2. Generiraj zadatke za sve (provider × strategy × question) parove
        tasks = []
        for provider_name, provider in providers.items():
            for code in strategy_codes:
                for q in questions:
                    tasks.append(self._evaluate_one(q, code, provider))

        # 3. Paralelno izvrši (uz semaphore za rate-limit kontrolu)
        logger.info("benchmark.tasks.scheduled", count=len(tasks))
        t_start = time.perf_counter()
        results: list[BenchmarkQuestionResult] = []
        completed = 0
        for coro in asyncio.as_completed(tasks):
            r = await coro
            results.append(r)
            completed += 1
            if completed % 10 == 0 or completed == len(tasks):
                elapsed = time.perf_counter() - t_start
                logger.info(
                    "benchmark.progress",
                    completed=completed,
                    total=len(tasks),
                    elapsed_s=round(elapsed, 1),
                )

        # 4. Izvrši gold SQL za svako jedinstveno pitanje — execution accuracy
        gold_results = await self._collect_gold_results(questions)

        # 5. Agregiraj metrike
        aggregates = compute_metrics(results, gold_results)

        # 6. (opcionalno) Security suite — wrap u try/except da neuspjeh
        # security suite-a (npr. rate limit, mreža) ne uništi benchmark
        # rezultate koje smo već skupili u prethodnim koracima.
        security_report: SecurityReport | None = None
        if include_security:
            sec_provider_name = security_provider_name or next(iter(providers))
            sec_provider = providers[sec_provider_name]
            logger.info("benchmark.security.start", provider=sec_provider_name)
            try:
                security_report = await run_security_suite(
                    validator=self._validator,
                    provider=sec_provider,
                    prompt_builder=self._prompt_builder,
                )
            except Exception as exc:
                logger.exception("benchmark.security.failed")
                print(f"⚠️  Security suite je failao ({exc}); benchmark rezultati zadržani.")

        finished_at = datetime.now(UTC).isoformat()
        logger.info(
            "benchmark.run.done",
            run_id=run_id,
            results=len(results),
            duration_s=round(time.perf_counter() - t_start, 1),
        )

        return BenchmarkRun(
            run_id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            config=config,
            question_results=results,
            aggregates=aggregates,
            security_report=security_report,
        )

    async def _evaluate_one(
        self,
        question: BirdQuestion,
        strategy_code: str,
        provider: BaseLLMProvider,
    ) -> BenchmarkQuestionResult:
        """Wrap evaluacije u semaforu da kontroliramo paralelne LLM pozive."""

        async with self._semaphore:
            try:
                return await self._service.evaluate(question, strategy_code, provider)
            except Exception as exc:
                # Defanzivno — jedan failed question ne smije srušiti cijeli run.
                logger.exception("benchmark.question.exception",
                                 question_id=question.question_id, strategy=strategy_code)
                return BenchmarkQuestionResult(
                    question_id=question.question_id,
                    db_id=question.db_id,
                    question=question.question,
                    difficulty=question.difficulty,
                    strategy=strategy_code,
                    provider=provider.name(),
                    gold_sql=question.gold_sql,
                    predicted_sql=None,
                    normalized_sql=None,
                    validated=False,
                    executed=False,
                    blocked=False,
                    error_reason=f"exception: {exc}",
                    retry_count=0,
                    llm_ms=0.0,
                    validation_ms=0.0,
                    execution_ms=0.0,
                    total_ms=0.0,
                    input_tokens=None,
                    output_tokens=None,
                    predicted_columns=[],
                    predicted_rows=[],
                )

    async def _collect_gold_results(
        self, questions: list[BirdQuestion]
    ) -> dict[int, list[list[Any]] | None]:
        """Izvrši gold SQL za svako jedinstveno question_id i cache-iraj rezultat.

        DESIGN NOTES (naučeno kroz produkcijske probleme):
        - **Concurrency = 3, ne više**: SQLite ima per-file lock. 10 paralelnih
          konekcija nad istom bazom (npr. ``card_games``) → resource starvation,
          neki query-ji uspijevaju, drugi se beskonačno zaglave i nikad ne
          dostignu vlastiti 60s timeout. Tested empirijski: 10 = hang na 50+ min,
          3 = završi predvidljivo.
        - **Per-question timeout=90s**: već postoji u executor-u, ovdje nije
          duplo.
        - **Overall hard cap = 300s (5 min)**: ako sve drugo padne, ne dopuštamo
          gold collection-u da pojede ostatak benchmarka. Bolje nekoliko
          neizmjerenih pitanja nego cijeli run propali.
        - **Gracefull degradation**: pitanja čije gold ne uspijemo dobiti
          (timeout, broken SQL) → None → EX se ne broji za njih, ali
          ostali rezultati spremaju se i analiziraju normalno.
        """

        # Skup jedinstvenih (question_id, BirdQuestion) parova — gold ne računamo
        # više puta za isto pitanje (npr. ako 4 strategije sve evaluiraju Q5).
        unique: dict[int, BirdQuestion] = {}
        for q in questions:
            unique.setdefault(q.question_id, q)

        sem = asyncio.Semaphore(3)  # SQLite-safe concurrency
        results: dict[int, list[list[Any]] | None] = {qid: None for qid in unique}

        async def _gold_for_one(q: BirdQuestion) -> None:
            async with sem:
                res = await self._service.execute_gold(q)
                results[q.question_id] = res[1] if res is not None else None

        try:
            await asyncio.wait_for(
                asyncio.gather(*(_gold_for_one(q) for q in unique.values())),
                timeout=300.0,  # 5 min hard cap
            )
        except TimeoutError:
            done = sum(1 for v in results.values() if v is not None)
            logger.warning(
                "benchmark.gold.collection_timeout",
                done=done,
                total=len(unique),
                missing=len(unique) - done,
            )

        return results


def _make_run_id() -> str:
    """Run ID = ISO timestamp slug, bez specijalnih znakova za file imena."""

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


# ----------------------------------------------------------------------
# Serijalizacija u JSON — odvojeno radi clarity-ja
# ----------------------------------------------------------------------


def run_to_dict(run: BenchmarkRun) -> dict[str, Any]:
    """Konvertira ``BenchmarkRun`` u serijalizabilan dict za JSON export."""

    return {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "config": run.config,
        "aggregates": [aggregates_to_dict(a) for a in run.aggregates.values()],
        "question_results": [_question_to_dict(q) for q in run.question_results],
        "security": _security_to_dict(run.security_report) if run.security_report else None,
    }


def _question_to_dict(r: BenchmarkQuestionResult) -> dict[str, Any]:
    """Per-question detalji za JSON. Cell-stringify za stabilnost serijalizacije."""

    return {
        "question_id": r.question_id,
        "db_id": r.db_id,
        "question": r.question,
        "difficulty": r.difficulty,
        "strategy": r.strategy,
        "provider": r.provider,
        "gold_sql": r.gold_sql,
        "predicted_sql": r.predicted_sql,
        "normalized_sql": r.normalized_sql,
        "validated": r.validated,
        "executed": r.executed,
        "blocked": r.blocked,
        "error_reason": r.error_reason,
        "retry_count": r.retry_count,
        "llm_ms": round(r.llm_ms, 2),
        "validation_ms": round(r.validation_ms, 2),
        "execution_ms": round(r.execution_ms, 2),
        "total_ms": round(r.total_ms, 2),
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "predicted_columns": r.predicted_columns,
        # Ćelije serijaliziramo kao stringove jer JSON ne podržava sve
        # Python tipove (Decimal, datetime). Predicted_rows su informativni —
        # comparator radi vlastite usporedbe in-memory u Python tipovima.
        "predicted_rows": [[_stringify(cell) for cell in row] for row in r.predicted_rows],
    }


def _stringify(value: Any) -> Any:
    """JSON-safe konverzija ćelije — primitive ostaju, ostalo str()."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _security_to_dict(report: SecurityReport) -> dict[str, Any]:
    return {
        "direct_sql_rejection_rate": round(report.direct_sql_rejection_rate, 4),
        "nl_pipeline_rejection_rate": round(report.nl_pipeline_rejection_rate, 4),
        "overall_security_score": round(report.overall_security_score, 4),
        "direct_sql": [
            {"sql": r.sql, "blocked": r.blocked, "reason": r.blocked_reason}
            for r in report.direct_sql_results
        ],
        "nl_pipeline": [
            {
                "question": r.question,
                "generated_sql": r.generated_sql,
                "refused_by_llm": r.refused_by_llm,
                "blocked_by_validator": r.blocked_by_validator,
                "would_execute": r.executed,
                "blocked_reason": r.blocked_reason,
            }
            for r in report.nl_pipeline_results
        ],
    }
