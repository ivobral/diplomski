"""LLM-as-Judge: semantička verifikacija da li rezultat odgovara pitanju.

Akademska referenca: "Self-Refine: Iterative Refinement with Self-Feedback"
(Madaan et al. 2023) — LLM može učinkovito kritizirati vlastiti output i
identificirati greške koje heuristika ne hvata.

Razlog uvođenja: ``verify_result`` heuristika (`services/result_verifier.py`)
hvata očite anomalije (0 rows na "list", multi-row na "count") ali propušta
**semantički netočne** rezultate koji izgledaju "normalno":

- Pitanje traži škole, vratili smo gradove → izgleda OK ali nije
- Pitanje traži top 5, vratili smo 12 → struktura tipa, ali wrong N
- Pitanje implicira agregaciju kojeg gold radi DISTINCT, naš ne

LLM-as-Judge je **dodatni okidač** za retry — Cascade v3 trigerira Layer 3
(self-consistency) ako EITHER heuristika sumnja ILI judge kaže "ne odgovara".

Konzervativan po dizajnu — bolje propustiti par sumnjivih nego lažno označiti
i potrošiti retry budget na zapravo točan rezultat. To je "do no harm"
filozofija: ako judge nije siguran, NE trigeriramo retry.

API:
    is_wrong, reason = await llm_judge_result(
        question, sql, columns, rows, provider
    )
    # is_wrong=True znači "judge je confident da je rezultat netočan"
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.llm.base import BaseLLMProvider, Prompt

logger = logging.getLogger(__name__)

# Truncate rows poslane LLM-u — full result može biti tisuće redaka. Sample je
# dovoljan da LLM procijeni "izgleda li ovo razumno". 5 redaka × 10 kolona
# ostavlja prompt malim a sačuvava signal.
_MAX_ROWS_IN_PROMPT = 5
_MAX_COLS_IN_PROMPT = 10
_MAX_VALUE_LENGTH = 60  # truncate dugačke string values

_JUDGE_SYSTEM = """You are a strict but conservative evaluator of SQL query results.

Your job: given a natural-language question and the rows that an SQL query \
returned, decide whether the result PLAUSIBLY answers the question.

Be CONSERVATIVE: only respond "WRONG" when you are confident the result is \
incorrect. If unsure, respond "OK". A false alarm wastes compute on retries; \
a missed wrong answer is acceptable risk.

Common wrong-answer signals:
- Question asks for X (e.g., school names) but result contains Y (e.g., city names)
- Question asks for a single value (max, average, count) but result is a list of rows
- Question asks for top N but result has different cardinality
- Question expects aggregation but result shows ungrouped raw rows

Respond ONLY with JSON: {"verdict": "OK" or "WRONG", "reason": "<short explanation>"}
No prose before or after the JSON.
"""

_JUDGE_USER_TEMPLATE = """Question:
{question}

SQL executed:
{sql}

Result columns:
{columns}

Result rows (showing first {n_shown} of {n_total}):
{rows}

Does this result plausibly answer the question? Respond with the JSON verdict.
"""


async def llm_judge_result(
    question: str,
    sql: str,
    columns: list[str],
    rows: list[list[Any]],
    provider: BaseLLMProvider,
) -> tuple[bool, str]:
    """Procijeni je li SQL rezultat semantički kompatibilan s pitanjem.

    Args:
        question: original natural-language pitanje.
        sql: izvršen SQL upit.
        columns: imena vraćenih kolona.
        rows: vraćene retke (može biti velik — truncate-amo).
        provider: LLM provider za poziv judge-a.

    Returns:
        ``(is_wrong, reason)`` — ako ``True``, judge je confident da je
        rezultat netočan i predlaže retry. Ako ``False``, ne intervenira.
        Default na ``False`` (do-no-harm) pri bilo kojoj grešci u judge-u.
    """

    # Format rows za prompt — truncate width and depth da držimo prompt small
    cols_display = columns[:_MAX_COLS_IN_PROMPT]
    if len(columns) > _MAX_COLS_IN_PROMPT:
        cols_display.append(f"... ({len(columns) - _MAX_COLS_IN_PROMPT} more)")

    rows_display = []
    for row in rows[:_MAX_ROWS_IN_PROMPT]:
        truncated_row = []
        for v in row[:_MAX_COLS_IN_PROMPT]:
            s = str(v) if v is not None else "NULL"
            if len(s) > _MAX_VALUE_LENGTH:
                s = s[: _MAX_VALUE_LENGTH - 3] + "..."
            truncated_row.append(s)
        rows_display.append(truncated_row)

    rows_str = "\n".join(str(r) for r in rows_display) if rows_display else "(empty)"

    prompt = Prompt(
        system=_JUDGE_SYSTEM,
        user=_JUDGE_USER_TEMPLATE.format(
            question=question,
            sql=sql[:1500],  # also truncate SQL ako je dugačak
            columns=cols_display,
            n_shown=len(rows_display),
            n_total=len(rows),
            rows=rows_str,
        ),
    )

    try:
        response = await provider.generate(prompt)
    except Exception as exc:
        # Judge greška NIJE razlog za retry — do no harm.
        logger.debug("judge.llm_call_failed", extra={"error": str(exc)})
        return False, f"judge_unavailable: {exc}"

    verdict, reason = _parse_judge_response(response.raw_text)
    return (verdict == "WRONG"), reason


def _parse_judge_response(raw: str) -> tuple[str, str]:
    """Robust parsiranje judge JSON-a.

    LLM ponekad obavlja JSON markdown blokovima ili dodaje preamble.
    Skupimo JSON objekt iz teksta i izvučemo verdict + reason.
    """

    # Strip markdown code blocks if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Skip language hint line, find closing fence
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

    # Find first JSON object
    match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
    if not match:
        # Fallback: ako nema JSON-a, parsiraj prema keyword-ima u tekstu
        upper = cleaned.upper()
        if "WRONG" in upper and "OK" not in upper:
            return "WRONG", "parsed_from_text"
        return "OK", "no_json_default_ok"

    try:
        obj = json.loads(match.group(0))
        verdict = str(obj.get("verdict", "OK")).strip().upper()
        reason = str(obj.get("reason", ""))[:200]
        if verdict not in ("OK", "WRONG"):
            return "OK", "invalid_verdict_default_ok"
        return verdict, reason
    except json.JSONDecodeError:
        return "OK", "json_parse_failed_default_ok"
