"""Entity extraction pre-step za D strategiju.

Pomaže s `wrong_filter` failure tipom — pitanja gdje LLM napiše SQL s
literalom koji ne postoji u bazi (case mismatch, sinonim, kraći oblik).
Primjer: pitanje spominje "San Bernardino" ali u bazi piše "san bernardino"
(lowercase) — bez ovog koraka LLM uvijek piše "San Bernardino" i query
vrati 0 redaka.

Pre-step se sastoji od dva sub-stepa:
1. **LLM ekstrakcija**: identificira nazive entitete iz pitanja
   (osobe, mjesta, dates, statuse).
2. **DB mapping**: za svaki entitet, traži najsličnije vrijednosti u
   bazi po koloni (LIKE %literal% / case-insensitive equal).

Rezultat: lista (entity_text, suggested_db_values) parova koja se
uvrsti u glavni prompt kao "Entity hints" blok. LLM tada zna stvarne
vrijednosti za WHERE klauzule.

Akademska referenca: koncept je sličan "named entity recognition" iz NLP-a,
ali fokusiran na SQL: ne klasificiramo tipove (PERSON/LOC), nego mapiramo
na DB column vrijednosti. Slično "value linking" u DAIL-SQL paperu.
"""

from __future__ import annotations

import json
import re

from app.llm.base import Prompt

ENTITY_EXTRACTION_SYSTEM = """\
You are a named-entity extractor for SQL filtering tasks. Given a question,
identify proper nouns and specific literal values that will likely appear
in WHERE clauses of a SQL query.

OUTPUT FORMAT (strict):
- JSON array of strings, where each string is a value to look up in the database.
- Include: person names, place names, organization names, status codes,
  specific dates, specific numbers used as identifiers (e.g., zip codes).
- Exclude: generic terms ("schools", "customers"), aggregations
  ("highest", "most"), units ("dollars", "years").

Example input: "How many female clients in East Bohemia have salary > 6000?"
Example output: ["East Bohemia", "female"]

Example input: "List schools in San Bernardino County opened after 2010."
Example output: ["San Bernardino", "San Bernardino County"]

If there are no extractable literal entities, return [].
"""


ENTITY_EXTRACTION_USER_TEMPLATE = """\
Question: {question}
{evidence_block}
Entities (JSON array):"""


def build_entity_extraction_prompt(question: str, evidence: str = "") -> Prompt:
    """Build prompt za entity extraction pre-step.

    Args:
        question: korisničko pitanje.
        evidence: opcionalan BIRD hint (pomaže razumijevanju).
    """

    evidence_block = ""
    if evidence.strip():
        evidence_block = f"Expert hint: {evidence.strip()}\n"

    user_prompt = ENTITY_EXTRACTION_USER_TEMPLATE.format(
        question=question,
        evidence_block=evidence_block,
    )
    return Prompt(system=ENTITY_EXTRACTION_SYSTEM, user=user_prompt)


def parse_entities(raw_response: str) -> list[str]:
    """Parsira LLM odgovor (JSON array) u listu string entitete.

    Defenzivno na:
    - markdown code blocks
    - dodatni prose oko JSON-a
    - prazan/null odgovor
    - non-string elementi (filtriraju se)
    """

    text = raw_response.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```$", "", text)

    parsed: list | None = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Pokušaj naći [..] substring
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    if not isinstance(parsed, list):
        return []

    # Filter na string elemente, dedupliciraj zadržavajući redoslijed
    seen: set[str] = set()
    result: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(normalized)
    return result
