"""PromptBuilder — orchestrator nad strategijama i template-ima.

Odgovornost: dohvati shemu (po potrebi) i konstruira finalni ``Prompt``
DTO koji ide LLM provideru. RetryEngine također koristi ovaj builder za
build-anje retry prompt-a — jedan modul, jedan izvor istine o tome kako
izgleda prompt.
"""

from __future__ import annotations

from app.db.schema_inspector import DatabaseSchema, SchemaInspector
from app.llm.base import Prompt
from app.llm.prompts.strategies import PromptStrategy, StrategyA
from app.llm.prompts.templates import (
    RETRY_TEMPLATE,
    format_schema_for_prompt,
    get_system_prompt,
)


class PromptBuilder:
    """Sastavlja ``Prompt`` objekte za inicijalnu generaciju i retry.

    Dialect-aware: konstruktor prima default dialect koji se koristi za
    odabir system prompta (PostgreSQL vs SQLite). Pojedinačni pozivi mogu
    override-ati per request (parametar ``dialect``) — koristi se za
    benchmark gdje BIRD baze tjeraju SQLite mode.
    """

    def __init__(
        self,
        schema_inspector: SchemaInspector,
        default_dialect: str = "postgres",
    ) -> None:
        self._inspector = schema_inspector
        self._default_dialect = default_dialect

    async def build(
        self,
        question: str,
        strategy: PromptStrategy,
        dialect: str | None = None,
        schema_override: DatabaseSchema | None = None,
        evidence: str = "",
        column_descriptions: dict[tuple[str, str], str] | None = None,
        decomposition: str = "",
    ) -> Prompt:
        """Build inicijalni prompt prema strategiji.

        Args:
            question: korisničko pitanje.
            strategy: A/B/C/D strategija konstrukcije user prompta.
            dialect: opcionalan override (npr. ``"sqlite"`` za benchmark).
            schema_override: opcionalan eksplicitan schema (za BIRD per-db).
            evidence: opcionalan BIRD expert hint. Samo StrategyD ga koristi;
                ostale ga ignoriraju (clean experiment: C vs D razlika).
            column_descriptions: opcionalan map (table, col) → human description.
                Samo D ih koristi (BIRD database_description CSV-ovi).

        Strategija A je jedina koja ne treba shemu — za nju preskačemo
        dohvat sheme da uštedimo round-trip prema bazi.
        """

        effective_dialect = dialect or self._default_dialect

        if isinstance(strategy, StrategyA):
            user_prompt = strategy.build_user_prompt(
                question, None, evidence, column_descriptions, decomposition
            )
        else:
            schema = (
                schema_override
                if schema_override is not None
                else await self._inspector.get_schema()
            )
            user_prompt = strategy.build_user_prompt(
                question, schema, evidence, column_descriptions, decomposition
            )

        return Prompt(system=get_system_prompt(effective_dialect), user=user_prompt)

    async def build_retry(
        self,
        question: str,
        previous_sql: str,
        errors: list[str],
        dialect: str | None = None,
        schema_override: DatabaseSchema | None = None,
        column_descriptions: dict[tuple[str, str], str] | None = None,
        evidence: str = "",
        decomposition: str = "",
    ) -> Prompt:
        """Build retry prompt nakon failed validacije.

        Retry prompt uvijek uključuje **maksimalan** kontekst — punu shemu
        (s FK relacijama), sample rows ako su dostupni, column descriptions
        ako su dostupni, plus evidence iz BIRD-a. Logika: retry je "skupo"
        (još jedan LLM poziv), pa nema smisla skrivati bilo što što imamo.

        Args:
            column_descriptions: opcionalan BIRD column description map.
            evidence: opcionalan BIRD evidence hint.
        """

        effective_dialect = dialect or self._default_dialect

        schema = (
            schema_override
            if schema_override is not None
            else await self._inspector.get_schema()
        )
        # Retry uvijek koristi full obogaćen format — schema s sample rows-ima
        # i column descriptions ako su pruženi. Ako nisu (UI demo), formatter
        # tiho izostavi.
        schema_text = format_schema_for_prompt(
            schema,
            include_relations=True,
            include_sample_rows=True,
            column_descriptions=column_descriptions,
        )

        errors_text = "\n".join(f"- {e}" for e in errors)

        # Ako imamo evidence + decomposition, prepend oba u question blok.
        # RETRY_TEMPLATE ne mijenjamo (jer ga koristi i UI demo retry koji
        # nema evidence/decomposition); umjesto toga oba uvlačimo u
        # question string.
        question_block = question
        extras: list[str] = []
        if evidence.strip():
            extras.append(f"Expert hint: {evidence.strip()}")
        if decomposition.strip():
            extras.append(f"Planning steps:\n{decomposition.strip()}")
        if extras:
            question_block = f"{question}\n\n" + "\n\n".join(extras)

        user_prompt = RETRY_TEMPLATE.format(
            previous_sql=previous_sql,
            errors=errors_text,
            schema=schema_text,
            question=question_block,
        )
        return Prompt(system=get_system_prompt(effective_dialect), user=user_prompt)
