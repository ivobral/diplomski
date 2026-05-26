"""Question decomposition pre-step za kompleksna pitanja u D strategiji.

Failure analyzer pokazuje da je **multiple_issues** najveća kategorija
neuspjeha (14/24 wrong_result u zadnjem run-u). To su pitanja s 2+
komponente — npr. _"List X and their Y where Z, sorted by W"_ — gdje
LLM griješi na barem jednoj. Decomposition strategija: prije generiranja
SQL-a, LLM **dekomponira** pitanje u 2-4 podpitanja/koraka, koji onda
služe kao chain-of-thought scaffold za glavni SQL poziv.

Razlika od evidence-a: evidence je human-written hint o semantici;
decomposition je AI-generirani plan kako pristupiti pitanju strukturalno.

Literatura: DIN-SQL (Pourreza & Rafiei, 2023) koristi sličnu ideju s
"problem decomposition" kao prvi step pipeline-a. Prijavljuju ~10 pp
boost na challenging BIRD pitanjima.
"""

from __future__ import annotations

from app.llm.base import Prompt

DECOMPOSITION_SYSTEM = """\
You are a SQL planner. Given a natural-language question that may be complex
(involving multiple filters, joins, aggregates, or sub-questions), decompose
it into clear analytical steps that another agent will use to write SQL.

OUTPUT FORMAT (strict):
- 2-4 numbered steps, one per line.
- Each step describes a logical operation in plain English.
- No SQL syntax. No prose introduction. Just the numbered steps.

Examples:

Question: List the names of schools with more than 30 difference in enrollments
between K-12 and ages 5-17 and give their full street address.
Steps:
1. Filter schools where (Enrollment K-12 - Enrollment Ages 5-17) > 30
2. Join with the address details for those schools
3. Select school name and full street address

Question: How many female clients live in the East Bohemia district?
Steps:
1. Find clients with gender = 'F'
2. Join clients with their district (district_id)
3. Filter where district matches East Bohemia
4. Count the resulting rows
"""


DECOMPOSITION_USER_TEMPLATE = """\
Question: {question}
{evidence_block}
Steps:"""


def build_decomposition_prompt(question: str, evidence: str = "") -> Prompt:
    """Build prompt za question decomposition step.

    Args:
        question: korisničko pitanje.
        evidence: opcionalan BIRD evidence — pomaže planneru.
    """

    evidence_block = ""
    if evidence.strip():
        evidence_block = f"Expert hint: {evidence.strip()}\n"

    user_prompt = DECOMPOSITION_USER_TEMPLATE.format(
        question=question,
        evidence_block=evidence_block,
    )
    return Prompt(system=DECOMPOSITION_SYSTEM, user=user_prompt)


def parse_decomposition(raw_response: str) -> str:
    """Sanitizira LLM odgovor za uvrstavanje u glavni SQL prompt.

    Vraća string spreman za "Decomposition steps:\\n{steps}" blok.
    Defenzivno: skida code-block markup, prefix-e, leading text prije
    prvog koraka. Ako parsing pada, vraća prazno (decomposition tada
    nema učinak na main prompt — tihi fallback).
    """

    if not raw_response.strip():
        return ""

    # Strip markdown
    text = raw_response.strip()
    if text.startswith("```"):
        # Ukloni first i last code fence
        lines = text.split("\n")
        # First line is "```" or "```text"
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Drop "Steps:" prefix if present
    text = text.lstrip()
    for prefix in ("Steps:", "steps:", "STEPS:", "Decomposition:"):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip()
            break

    return text
