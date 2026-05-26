"""Prompt strategije A / B / C / D.

Strategija definira **koliko konteksta** dobiva LLM u user prompt-u.
To je ključna varijabla koju diplomski rad analizira u Fazi 4 —
pokazuje utjecaj sheme i relacija na točnost.

| Strategija | User prompt sadrži                          |
|------------|---------------------------------------------|
| A          | samo pitanje (bez sheme)                    |
| B          | pitanje + shema (tablice + kolone)          |
| C          | pitanje + shema + FK relacije               |
| D          | C + BIRD evidence hint + retry mehanizam    |

Strategija D je "full kitchen sink" — kombinira:
- najbogatiji prompt (shema + FK relacije),
- BIRD expert evidence hint kad je dostupan (samo benchmark, ne demo),
- retry petlju u QueryService sloju kad validacija ne prođe.

To je production setup koji ide u rad kao "naš najbolji rezultat".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.db.schema_inspector import DatabaseSchema
from app.llm.prompts.templates import (
    USER_TEMPLATE_QUESTION_ONLY,
    USER_TEMPLATE_WITH_DECOMPOSITION,
    USER_TEMPLATE_WITH_RELATIONS,
    USER_TEMPLATE_WITH_RELATIONS_AND_EVIDENCE,
    USER_TEMPLATE_WITH_SCHEMA,
    format_schema_for_prompt,
)


class PromptStrategy(ABC):
    """Bazna klasa za strategiju konstrukcije user prompt-a."""

    # Slovo strategije (A/B/C/D) — koristi se u logovima i benchmark-u.
    code: ClassVar[str]

    @abstractmethod
    def build_user_prompt(
        self,
        question: str,
        schema: DatabaseSchema | None,
        evidence: str = "",
        column_descriptions: dict[tuple[str, str], str] | None = None,
        decomposition: str = "",
    ) -> str:
        """Vraća user prompt string za zadanu strategiju.

        Args:
            question: korisničko pitanje (može biti na bilo kojem jeziku).
            schema: dohvaćena shema baze; ``None`` je dozvoljen samo za
                Strategiju A jer ona ne treba shemu.
            evidence: opcionalan expert hint iz BIRD dataseta. Strategije
                A/B/C ga ignoriraju; samo D ga ugrađuje u prompt.
            column_descriptions: opcionalan BIRD column description map.
                Samo D ga koristi (inline komentari u schema text-u).
            decomposition: opcionalan output decomposition pre-step-a
                (numbered steps koje LLM-planner generira). Samo D ga
                koristi kao scaffold u glavnom SQL prompt-u.
        """


class StrategyA(PromptStrategy):
    """Bez sheme — najminimalniji prompt. Baseline za benchmark."""

    code = "A"

    def build_user_prompt(
        self,
        question: str,
        schema: DatabaseSchema | None,
        evidence: str = "",
        column_descriptions: dict[tuple[str, str], str] | None = None,
        decomposition: str = "",
    ) -> str:
        return USER_TEMPLATE_QUESTION_ONLY.format(question=question)


class StrategyB(PromptStrategy):
    """Pitanje + shema (tablice + kolone, bez FK relacija)."""

    code = "B"

    def build_user_prompt(
        self,
        question: str,
        schema: DatabaseSchema | None,
        evidence: str = "",
        column_descriptions: dict[tuple[str, str], str] | None = None,
        decomposition: str = "",
    ) -> str:
        if schema is None:
            raise ValueError("StrategyB zahtjeva shemu (dobiven None).")
        schema_text = format_schema_for_prompt(schema, include_relations=False)
        return USER_TEMPLATE_WITH_SCHEMA.format(schema=schema_text, question=question)


class StrategyC(PromptStrategy):
    """Pitanje + shema + FK relacije. Najbogatiji prompt **bez** evidence-a."""

    code = "C"

    def build_user_prompt(
        self,
        question: str,
        schema: DatabaseSchema | None,
        evidence: str = "",
        column_descriptions: dict[tuple[str, str], str] | None = None,
        decomposition: str = "",
    ) -> str:
        if schema is None:
            raise ValueError("StrategyC zahtjeva shemu (dobiven None).")
        schema_text = format_schema_for_prompt(schema, include_relations=True)
        # NAMJERNO ignoriramo evidence — to je C-vs-D razlika koju mjerimo.
        return USER_TEMPLATE_WITH_RELATIONS.format(schema=schema_text, question=question)


class StrategyD(StrategyC):
    """Isti prompt kao C + sample rows + column descriptions + **BIRD evidence hint** + retry.

    D je "full kitchen sink" varijanta:
    - schema + FK (kao C)
    - sample rows (3 redaka po tablici) — case sensitivity & value awareness
    - BIRD column descriptions — semantičko značenje kolona
    - BIRD evidence (kad postoji) — semantic mapping pitanje → SQL
    - retry mehanizam u service sloju

    Retry se događa u QueryService/BenchmarkQueryService sloju.
    """

    code = "D"

    def build_user_prompt(
        self,
        question: str,
        schema: DatabaseSchema | None,
        evidence: str = "",
        column_descriptions: dict[tuple[str, str], str] | None = None,
        decomposition: str = "",
    ) -> str:
        if schema is None:
            raise ValueError("StrategyD zahtjeva shemu (dobiven None).")
        # include_sample_rows=True + column_descriptions: full kontekst za LLM.
        # Ako nešto od ovoga nedostaje (UI demo bez BIRD-a), tiho se izostavi
        # — formatter je defenzivan na None vrijednosti.
        schema_text = format_schema_for_prompt(
            schema,
            include_relations=True,
            include_sample_rows=True,
            column_descriptions=column_descriptions,
        )

        # Tri tier-a fallback-a:
        # 1. ima evidence + decomposition → najobogaćeniji template
        # 2. ima evidence (bez decomp) → standard WITH_EVIDENCE
        # 3. nema ništa (UI demo bez BIRD-a) → goli C-format
        has_evidence = bool(evidence.strip())
        has_decomp = bool(decomposition.strip())

        if has_evidence and has_decomp:
            return USER_TEMPLATE_WITH_DECOMPOSITION.format(
                schema=schema_text,
                evidence=evidence.strip(),
                decomposition=decomposition.strip(),
                question=question,
            )
        if has_evidence:
            return USER_TEMPLATE_WITH_RELATIONS_AND_EVIDENCE.format(
                schema=schema_text,
                evidence=evidence.strip(),
                question=question,
            )
        return USER_TEMPLATE_WITH_RELATIONS.format(schema=schema_text, question=question)


# ----------------------------------------------------------------------
# Strategy registry + factory funkcija
# ----------------------------------------------------------------------

_REGISTRY: dict[str, type[PromptStrategy]] = {
    "A": StrategyA,
    "B": StrategyB,
    "C": StrategyC,
    "D": StrategyD,
}


def get_strategy(name: str | None) -> PromptStrategy:
    """Vraća instancu strategije po imenu (slovu).

    Args:
        name: "A", "B", "C", "D" ili ``None``. ``None`` → default "D"
            (najbolja kvaliteta odgovora — to je production-ready setup).

    Raises:
        ValueError: za nepoznato slovo.
    """

    # None znači "korisnik nije naveo strategy u request-u" — koristimo D
    # jer je to najbogatiji setup s retry-em.
    key = (name or "D").upper()
    if key not in _REGISTRY:
        raise ValueError(f"Nepoznata strategija: {name!r}. Dozvoljeno: A, B, C, D.")
    return _REGISTRY[key]()
