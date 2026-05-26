"""Self-correction loop za neispravne SQL upite.

Kad validator vrati ``errors`` (parse / semantic), RetryEngine konstruira
novi prompt s greškama + dostupnim identifikatorima i šalje LLM-u da
popravi SQL. Ovo se ponavlja dok ne uspije ili dok se ne potroši
``max_attempts`` (default 2).

**Strogo pravilo (sigurnost)**: retry se NIKAD ne pokreće za safety-blocked
upite. Safety blok je signal potencijalnog napada (DROP, DELETE, multi-
statement) — davanje "druge šanse" smanjuje sigurnost. QueryService ovo
provodi (vidi orkestraciju u ``query_service.py``).

Filozofija: koristimo **isti** provider i model za retry (ne switch-amo na
moćniji model). To pojednostavljuje analizu rezultata u Fazi 4 (retry je
mjera "kvalitete prompt strategije", ne "kvalitete fallback modela").
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.core.timing import Timer
from app.db.schema_inspector import DatabaseSchema
from app.llm.base import BaseLLMProvider, LLMResponse
from app.llm.prompts.builder import PromptBuilder

logger = get_logger(__name__)


class RetryEngine:
    """Pokreće LLM iznova s feedbackom o greškama validatora."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        prompt_builder: PromptBuilder,
        max_attempts: int,
    ) -> None:
        self._provider = provider
        self._prompt_builder = prompt_builder
        self._max_attempts = max_attempts

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    async def attempt_correction(
        self,
        question: str,
        previous_sql: str,
        validation_errors: list[str],
        attempt_num: int,
        provider_override: BaseLLMProvider | None = None,
        dialect: str | None = None,
        schema_override: DatabaseSchema | None = None,
        column_descriptions: dict[tuple[str, str], str] | None = None,
        evidence: str = "",
    ) -> tuple[LLMResponse, float]:
        """Pošalji retry prompt i vrati novi LLM odgovor + ukupna latencija LLM-a.

        Args:
            question: izvorno korisničko pitanje.
            previous_sql: SQL koji je pao validaciju.
            validation_errors: liste poruka iz ``ValidationResult.errors``.
            attempt_num: 1-based brojač pokušaja (za logiranje).
            provider_override: opcionalan request-scoped provider. Ako je
                naveden, koristi se umjesto default-a iz konstruktora —
                tako da retry koristi isti provider kao i inicijalni
                LLM poziv kad je u request-u zadan override.

        Returns:
            (LLMResponse, latency_ms_promp_build) — drugi element je
            vrijeme provedeno na build-anju prompta (može biti relevantno
            za benchmark; LLM latencija je već u LLMResponse).
        """

        active_provider = provider_override or self._provider

        logger.info(
            "retry.triggered",
            attempt=attempt_num,
            max=self._max_attempts,
            provider=active_provider.name(),
            errors=validation_errors[:3],  # prvih par za log preglednost
        )

        with Timer() as t:
            retry_prompt = await self._prompt_builder.build_retry(
                question=question,
                previous_sql=previous_sql,
                errors=validation_errors,
                dialect=dialect,
                schema_override=schema_override,
                column_descriptions=column_descriptions,
                evidence=evidence,
            )

        llm_response = await active_provider.generate(retry_prompt)
        logger.info(
            "retry.llm.responded",
            attempt=attempt_num,
            llm_ms=llm_response.latency_ms,
            sql_preview=llm_response.sql[:120],
        )
        return llm_response, t.elapsed_ms
