"""Testovi za compute_metrics — agregator BIRD result-a.

Provjeravamo da:
- Grupiranje po (provider, strategy) radi
- EX/EM/Validation rate ispravno računaju
- Blocked / error klasifikacija je točna
- Latency i token sumi se točno akumuliraju
"""

from __future__ import annotations

from app.evaluation.metrics import compute_metrics
from app.services.benchmark_query_service import BenchmarkQuestionResult


def _result(
    qid: int,
    strategy: str = "D",
    provider: str = "test-provider",
    gold_sql: str = "SELECT 1",
    predicted_sql: str | None = "SELECT 1",
    normalized_sql: str | None = "SELECT 1",
    validated: bool = True,
    executed: bool = True,
    blocked: bool = False,
    rows: list[list] | None = None,
    columns: list[str] | None = None,
    error_reason: str | None = None,
    retry_count: int = 0,
    llm_ms: float = 100.0,
    validation_ms: float = 10.0,
    execution_ms: float = 5.0,
    input_tokens: int | None = 50,
    output_tokens: int | None = 10,
    db_id: str = "db",
    question: str = "q?",
    difficulty: str = "simple",
) -> BenchmarkQuestionResult:
    """Helper za konstrukciju test BenchmarkQuestionResult-a."""

    return BenchmarkQuestionResult(
        question_id=qid,
        db_id=db_id,
        question=question,
        difficulty=difficulty,
        strategy=strategy,
        provider=provider,
        gold_sql=gold_sql,
        predicted_sql=predicted_sql,
        normalized_sql=normalized_sql,
        validated=validated,
        executed=executed,
        blocked=blocked,
        error_reason=error_reason,
        retry_count=retry_count,
        llm_ms=llm_ms,
        validation_ms=validation_ms,
        execution_ms=execution_ms,
        total_ms=llm_ms + validation_ms + execution_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        predicted_columns=columns or ["c"],
        predicted_rows=rows if rows is not None else [[1]],
    )


class TestGrouping:
    def test_groups_by_provider_strategy(self) -> None:
        results = [
            _result(1, strategy="A", provider="p1"),
            _result(2, strategy="A", provider="p1"),
            _result(3, strategy="D", provider="p1"),
            _result(4, strategy="A", provider="p2"),
        ]
        gold = {1: [[1]], 2: [[1]], 3: [[1]], 4: [[1]]}
        agg = compute_metrics(results, gold)
        assert set(agg.keys()) == {("p1", "A"), ("p1", "D"), ("p2", "A")}
        assert agg[("p1", "A")].total == 2
        assert agg[("p1", "D")].total == 1


class TestExecutionAccuracy:
    def test_ex_correct(self) -> None:
        results = [_result(1, rows=[[42]])]
        gold = {1: [[42]]}
        agg = compute_metrics(results, gold)
        a = agg[("test-provider", "D")]
        assert a.execution_accuracy == 1
        assert a.execution_accuracy_rate == 1.0

    def test_ex_wrong(self) -> None:
        results = [_result(1, rows=[[99]])]
        gold = {1: [[42]]}
        agg = compute_metrics(results, gold)
        a = agg[("test-provider", "D")]
        assert a.execution_accuracy == 0

    def test_ex_skipped_if_no_gold(self) -> None:
        """Gold execution failed → pitanje se NE broji u EX."""

        results = [_result(1, rows=[[42]])]
        gold = {1: None}
        agg = compute_metrics(results, gold)
        a = agg[("test-provider", "D")]
        assert a.execution_accuracy == 0  # ne broji se kao "correct"


class TestValidationRate:
    def test_validated_counted(self) -> None:
        results = [
            _result(1, validated=True),
            _result(2, validated=False),
            _result(3, validated=True),
        ]
        gold = {1: [[1]], 2: [[1]], 3: [[1]]}
        agg = compute_metrics(results, gold)
        a = agg[("test-provider", "D")]
        assert a.validation_success == 2
        assert a.validation_success_rate == pytest.approx(2 / 3)


class TestBlocked:
    def test_blocked_counted_separately(self) -> None:
        """Blocked je svoja klasa — NIJE error i nije validation success."""

        results = [
            _result(1, blocked=True, validated=False, executed=False, error_reason="DDL"),
        ]
        gold = {1: None}
        agg = compute_metrics(results, gold)
        a = agg[("test-provider", "D")]
        assert a.blocked == 1
        assert a.error == 0  # blocked se NE broji kao error


class TestTokensAccumulation:
    def test_tokens_summed(self) -> None:
        results = [
            _result(1, input_tokens=100, output_tokens=20),
            _result(2, input_tokens=50, output_tokens=10),
        ]
        gold = {1: [[1]], 2: [[1]]}
        agg = compute_metrics(results, gold)
        a = agg[("test-provider", "D")]
        assert a.total_input_tokens == 150
        assert a.total_output_tokens == 30


# pytest import koji koristimo za approx
import pytest  # noqa: E402
