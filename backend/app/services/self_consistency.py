"""Self-consistency strategija za D pipeline.

Akademska referenca: Wang et al. 2022 ("Self-Consistency Improves Chain of
Thought Reasoning in Language Models") + DAIL-SQL primjena (Gao et al. 2023).

Ideja: umjesto jednog LLM poziva, generiraj N=5 SQL varijanti, izvrši
svaku, pa **glasaj na rezultatu** (ne na SQL string-u). Rezultat koji se
najviše puta pojavljuje je najvjerojatnije točan.

Razlog: dva SQL upita s drugačijim sintaktičkim oblikom ali istim
rezultatom su SEMANTIČKI ISTI. Strict EM bi ih dao false negative;
self-consistency ih svrstava u istu klasu i broji glasove.

**Trošak**: 5x glavni LLM poziv. Za D pipeline koji ima 3-5 calls per
question, to znači ~6-9 calls per question. Veliki trošak ali najjači
single boost iz literature (typično +10-15 pp EX).

Implementacija:
1. Pozovi LLM N puta s istim promptom (small variation through temperature
   ili sampling). GPT-5 reasoning modeli ne podržavaju temperature ≠ 1,
   ali ipak generiraju različite output-e zbog internog reasoning sampling-a.
2. Validiraj svaki SQL kroz standard validator pipeline.
3. Izvrši svaki validan SQL i prikupi rezultate.
4. Klasificiraj rezultate na grupe (rows_equal multiset semantics).
5. Vrati najveću grupu kao "consensus answer"; tie-breaker = shortest SQL.

Failsafe: ako nijedan od N nije izvršiv, vrati najbolji valid kandidat
(ili prvi ako svi failuju).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.evaluation.comparators import rows_equal
from app.llm.base import BaseLLMProvider, LLMResponse, Prompt
from app.services.benchmark_executor import BenchmarkExecutor

logger = logging.getLogger(__name__)


# Default broj samples-a — 5 je optimal po Wang et al.; manje = manji boost,
# više = brzo padajući prinos uz veliki trošak.
DEFAULT_N_SAMPLES = 5


@dataclass(slots=True)
class CandidateResult:
    """Jedan od N samples-a u self-consistency procesu."""

    sql: str
    raw_response: str
    validated: bool
    normalized_sql: str | None
    executed: bool
    columns: list[str]
    rows: list[list[Any]]
    error: str | None
    llm_ms: float
    input_tokens: int | None
    output_tokens: int | None


@dataclass(slots=True)
class ConsensusResult:
    """Krajnji output self-consistency procesa."""

    chosen: CandidateResult
    all_candidates: list[CandidateResult]
    vote_count: int          # koliko kandidata se slaže s chosen rezultatom
    total_executed: int      # koliko ih je uspješno izvršilo
    consensus_strength: float  # vote_count / total_executed


async def generate_candidates(
    prompt: Prompt,
    provider: BaseLLMProvider,
    n_samples: int = DEFAULT_N_SAMPLES,
) -> list[LLMResponse]:
    """Generira N samples-a paralelno s istim promptom.

    Reasoning modeli (GPT-5, o-serija) generiraju različite output-e svaki
    put zbog interne stohasticity. Klasični modeli (gpt-4o) bi trebali
    temperature > 0 ali to nije podržano za reasoning; idemo s default-om.

    Args:
        prompt: full prompt za main SQL generation.
        provider: aktivni LLM provider.
        n_samples: koliko sample-ova generirati (default 5).

    Returns:
        Lista N LLMResponse objekata. Greška u jednom call-u → ne ruši cijeli
        proces, samo se taj sample izostavi.
    """

    async def _safe_call(idx: int) -> LLMResponse | None:
        try:
            return await provider.generate(prompt)
        except Exception as exc:
            logger.warning("self_consistency.call_failed", extra={
                "sample_idx": idx, "error": str(exc),
            })
            return None

    # Paralelizacija: svaki call asinkrono. Provider već ima rate-limit retry.
    raw = await asyncio.gather(*[_safe_call(i) for i in range(n_samples)])
    return [r for r in raw if r is not None]


async def execute_and_vote(
    candidates: list[LLMResponse],
    validator,
    executor: BenchmarkExecutor,
    db_id: str,
    schema,
) -> ConsensusResult:
    """Validira + izvršava sve kandidate, voting po rezultatu.

    Args:
        candidates: lista LLMResponse iz ``generate_candidates``.
        validator: SqlValidator instance.
        executor: BenchmarkExecutor instance.
        db_id: BIRD database id.
        schema: filtered DatabaseSchema (za semantic check).

    Returns:
        ConsensusResult s odabranim kandidatom + statistikom.
        Ako niti jedan ne uspije, vraća prvi kandidat (degraded mode).
    """

    if not candidates:
        # Edge case: svi pozivi failali
        raise RuntimeError("Self-consistency: no candidates generated")

    # Step 1: za svaki kandidat — validate + execute
    processed: list[CandidateResult] = []
    for resp in candidates:
        processed.append(await _process_candidate(resp, validator, executor, db_id, schema))

    # Step 2: voting samo nad izvršenim kandidatima
    executed_candidates = [c for c in processed if c.executed]

    if not executed_candidates:
        # Niti jedan se nije izvršio — pick prvi validan (ili prvi uopće)
        validated = next((c for c in processed if c.validated), None)
        chosen = validated or processed[0]
        return ConsensusResult(
            chosen=chosen,
            all_candidates=processed,
            vote_count=1,
            total_executed=0,
            consensus_strength=0.0,
        )

    # Step 3: grupiranje po rezultatu (set-equality multiset semantics)
    groups: list[list[CandidateResult]] = []
    for cand in executed_candidates:
        placed = False
        for group in groups:
            # Usporedi s representative-om grupe
            rep = group[0]
            if _results_match(cand, rep):
                group.append(cand)
                placed = True
                break
        if not placed:
            groups.append([cand])

    # Step 4: pick largest group; tie-breaker = shortest SQL (heuristika
    # da najjednostavnije rješenje koje radi je vjerojatno najpravilnije)
    groups.sort(key=lambda g: (-len(g), len(g[0].sql)))
    winning_group = groups[0]
    chosen = winning_group[0]

    return ConsensusResult(
        chosen=chosen,
        all_candidates=processed,
        vote_count=len(winning_group),
        total_executed=len(executed_candidates),
        consensus_strength=len(winning_group) / len(executed_candidates),
    )


def _results_match(a: CandidateResult, b: CandidateResult) -> bool:
    """True ako dva kandidata vraćaju semantički isti rezultat."""

    if not a.executed or not b.executed:
        return False
    if len(a.columns) != len(b.columns):
        return False
    return rows_equal(a.rows, b.rows, strict_order=False)


async def _process_candidate(
    resp: LLMResponse,
    validator,
    executor: BenchmarkExecutor,
    db_id: str,
    schema,
) -> CandidateResult:
    """Validira + izvršava jedan kandidat. Return-a CandidateResult."""

    sql = resp.sql

    # Validate
    try:
        v = await validator.validate(
            sql,
            dialect="sqlite",
            schema_override=schema,
            enforce_limit=False,
        )
    except Exception as exc:
        return CandidateResult(
            sql=sql, raw_response=resp.raw_text, validated=False,
            normalized_sql=None, executed=False, columns=[], rows=[],
            error=f"validation_error: {exc}",
            llm_ms=resp.latency_ms,
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
        )

    if not v.ok or not v.normalized_sql:
        return CandidateResult(
            sql=sql, raw_response=resp.raw_text, validated=False,
            normalized_sql=v.normalized_sql,
            executed=False, columns=[], rows=[],
            error=v.blocked_reason or "; ".join(v.errors),
            llm_ms=resp.latency_ms,
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
        )

    # Execute
    try:
        ex = await executor.execute(v.normalized_sql, db_id=db_id)
        return CandidateResult(
            sql=sql, raw_response=resp.raw_text, validated=True,
            normalized_sql=v.normalized_sql,
            executed=True, columns=ex.columns, rows=ex.rows,
            error=None,
            llm_ms=resp.latency_ms,
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
        )
    except Exception as exc:
        return CandidateResult(
            sql=sql, raw_response=resp.raw_text, validated=True,
            normalized_sql=v.normalized_sql,
            executed=False, columns=[], rows=[],
            error=f"exec_error: {exc}",
            llm_ms=resp.latency_ms,
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
        )
