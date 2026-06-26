"""Metrike za benchmark evaluaciju.

Glavne metrike (po definiciji iz BIRD literature i diplomskog plana):

- **Exact Match (EM)** — generirani SQL i gold SQL su identični nakon
  normalizacije (sqlglot pretty-print oba, string eq).
- **Execution Accuracy (EX)** — set-equality rezultata izvršavanja
  (kroz comparators.rows_equal). Primarna BIRD metrika.
- **Validation Success Rate** — postotak generiranih SQL-ova koji prođu
  validator (parse + safety + semantic).
- **Error Rate** — postotak SQL-ova koji failuju validation ili execution.

Plus tri sigurnosne metrike (iz security_suite.py — direct_sql, nl_pipeline,
overall_security_score) i agregati: latency, token usage, breakdown po difficulty.

Sve metrike su agregirane po (provider, strategy) parovima — to je core
dimenzija eksperimenta u radu.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot.errors import ParseError

from app.evaluation.comparators import rows_equal
from app.services.benchmark_query_service import BenchmarkQuestionResult


@dataclass(slots=True)
class GroupAggregates:
    """Agregirane metrike za jedan (provider, strategy) par."""

    provider: str
    strategy: str
    total: int = 0
    exact_match: int = 0
    execution_accuracy: int = 0
    validation_success: int = 0
    blocked: int = 0
    error: int = 0
    retry_used: int = 0
    # Sums for averaging
    total_llm_ms: float = 0.0
    total_validation_ms: float = 0.0
    total_execution_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    # Total $ cost across all questions in this group (when pricing known).
    total_cost_usd: float = 0.0
    # Count of questions for which cost was computed (model in pricing table).
    cost_known_count: int = 0
    # Po difficulty
    by_difficulty: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    @property
    def exact_match_rate(self) -> float:
        return self.exact_match / self.total if self.total else 0.0

    @property
    def execution_accuracy_rate(self) -> float:
        return self.execution_accuracy / self.total if self.total else 0.0

    @property
    def validation_success_rate(self) -> float:
        return self.validation_success / self.total if self.total else 0.0

    @property
    def error_rate(self) -> float:
        return self.error / self.total if self.total else 0.0

    @property
    def mean_llm_ms(self) -> float:
        return self.total_llm_ms / self.total if self.total else 0.0

    @property
    def mean_total_ms(self) -> float:
        return (
            (self.total_llm_ms + self.total_validation_ms + self.total_execution_ms)
            / self.total
        ) if self.total else 0.0

    @property
    def mean_cost_usd(self) -> float:
        """Mean $ per question for which cost was computable."""

        return (
            self.total_cost_usd / self.cost_known_count
            if self.cost_known_count else 0.0
        )


def compute_metrics(
    results: list[BenchmarkQuestionResult],
    gold_results: dict[int, list[list[Any]] | None],
) -> dict[tuple[str, str], GroupAggregates]:
    """Izračunaj agregirane metrike po (provider, strategy) iz svih rezultata.

    Args:
        results: lista po-pitanju rezultata iz BenchmarkRunner-a.
        gold_results: mapping question_id → izvršeni gold redovi (ili None
            ako gold SQL nije uspjelo izvršiti). Bez gold rezultata se ne
            može izračunati execution accuracy.

    Returns:
        Mapping (provider_name, strategy_code) → GroupAggregates.
    """

    grouped: dict[tuple[str, str], GroupAggregates] = {}

    for r in results:
        key = (r.provider, r.strategy)
        if key not in grouped:
            grouped[key] = GroupAggregates(provider=r.provider, strategy=r.strategy)
        agg = grouped[key]

        agg.total += 1
        agg.total_llm_ms += r.llm_ms
        agg.total_validation_ms += r.validation_ms
        agg.total_execution_ms += r.execution_ms
        agg.total_input_tokens += r.input_tokens or 0
        agg.total_output_tokens += r.output_tokens or 0
        if r.cost_usd is not None:
            agg.total_cost_usd += r.cost_usd
            agg.cost_known_count += 1
        if r.retry_count > 0:
            agg.retry_used += 1

        # Status klasifikacija
        if r.blocked:
            agg.blocked += 1
        elif r.error_reason and not r.executed:
            agg.error += 1

        if r.validated:
            agg.validation_success += 1

        # Exact Match: usporedi normalizirani predicted s normaliziranim gold
        # (sqlglot pretty-print obje, string eq). Tek nakon validacije —
        # neispravan SQL nikad nije EM.
        if r.normalized_sql and r.gold_sql:
            try:
                norm_gold = _normalize_sql_safe(r.gold_sql)
                norm_pred = _normalize_sql_safe(r.normalized_sql)
                if norm_pred is not None and norm_gold is not None and norm_pred == norm_gold:
                    agg.exact_match += 1
            except Exception:
                # Defanzivno — ako sqlglot pukne, ne računamo EM ali ne
                # rušimo cijelu agregaciju.
                pass

        # Execution Accuracy: gold rezultati moraju postojati
        gold_rows = gold_results.get(r.question_id)
        if r.executed and gold_rows is not None:
            if rows_equal(r.predicted_rows, gold_rows, strict_order=False):
                agg.execution_accuracy += 1

        # Difficulty breakdown
        bucket = agg.by_difficulty[r.difficulty]
        bucket["total"] += 1
        if r.executed and gold_rows is not None and rows_equal(r.predicted_rows, gold_rows):
            bucket["execution_accuracy"] += 1

    return grouped


def _normalize_sql_safe(sql: str) -> str | None:
    """Pretty-print SQL kroz sqlglot za string-level EM usporedbu.

    SQLite dialect (BIRD je SQLite). Ako parsing pukne, vraćamo None —
    EM se ne broji za taj par.
    """

    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
        return parsed.sql(dialect="sqlite", pretty=True)
    except (ParseError, Exception):
        return None


def aggregates_to_dict(agg: GroupAggregates) -> dict[str, Any]:
    """Serijalizacijski helper za JSON export — vraća strukturirani dict."""

    return {
        "provider": agg.provider,
        "strategy": agg.strategy,
        "total": agg.total,
        "exact_match": agg.exact_match,
        "exact_match_rate": round(agg.exact_match_rate, 4),
        "execution_accuracy": agg.execution_accuracy,
        "execution_accuracy_rate": round(agg.execution_accuracy_rate, 4),
        "validation_success": agg.validation_success,
        "validation_success_rate": round(agg.validation_success_rate, 4),
        "blocked": agg.blocked,
        "error": agg.error,
        "error_rate": round(agg.error_rate, 4),
        "retry_used": agg.retry_used,
        "mean_llm_ms": round(agg.mean_llm_ms, 2),
        "mean_total_ms": round(agg.mean_total_ms, 2),
        "total_input_tokens": agg.total_input_tokens,
        "total_output_tokens": agg.total_output_tokens,
        "total_cost_usd": round(agg.total_cost_usd, 4),
        "mean_cost_usd": round(agg.mean_cost_usd, 6),
        "by_difficulty": {k: dict(v) for k, v in agg.by_difficulty.items()},
    }
