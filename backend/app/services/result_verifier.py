"""Execute-then-verify post-processing korak za D strategiju.

Failure analyzer pokazuje da postoji kategorija pitanja gdje SQL prolazi
validaciju I izvršava se uspješno (validated + executed), ali rezultat
nije ono što pitanje implicitno traži. Klasični primjeri:

- "List all schools where X" → SQL vrati **0 redaka** (vjerojatno wrong filter)
- "How many X" → SQL vrati **više redaka** (vjerojatno missing aggregate)
- "What is the max Y" → SQL vrati **non-numeric** ili **multi-row**

Execute-then-verify provjeri rezultat protiv očekivanog "shape-a" izvedenog
iz pitanja, i pokrene retry s konkretnom feedback porukom ako rezultat
izgleda sumnjivo.

To je low-cost intervencija (samo regex na pitanje) koja hvata edge case-ove
gdje LLM griješi semantički ali sustav inače ne primijeti.

Filozofija: detekcija je intencionalno **konservativna** — više volimo
propustiti par sumnjivih nego ih lažno označiti i potrošiti retry budžet
na zapravo točan rezultat.
"""

from __future__ import annotations

import re
from typing import Any

# ----------------------------------------------------------------------
# Detekcija očekivane forme rezultata iz pitanja
# ----------------------------------------------------------------------


def _question_expects_count(question: str) -> bool:
    """Pitanje očekuje JEDAN brojčani rezultat."""

    q = question.lower()
    triggers = [
        r"\bhow many\b",
        r"\bnumber of\b",
        r"\bcount of\b",
        r"\bno\.? of\b",        # "no. of districts"
        r"\btotal number\b",
    ]
    return any(re.search(t, q) for t in triggers)


def _question_expects_list(question: str) -> bool:
    """Pitanje traži više redaka (ne jedan-cell odgovor)."""

    q = question.lower()
    triggers = [
        r"\blist\b",
        r"\bshow\b",
        r"\bgive (?:me )?the\b",
        r"\bwhich\b",
        r"\bwhat are\b",
        r"\benumerate\b",
        r"\bdisplay\b",
        r"\bfind (?:all|the)\b",
        r"\bnames? of\b",
        r"\btop \d+\b",
    ]
    return any(re.search(t, q) for t in triggers)


def _question_expects_single_value(question: str) -> bool:
    """Pitanje traži jednu vrijednost (max/min/avg/who/what)."""

    q = question.lower()
    triggers = [
        r"\bwhat is the\b",
        r"\bhighest\b",
        r"\blowest\b",
        r"\bmaximum\b",
        r"\bminimum\b",
        r"\baverage of\b",
        r"\bwho is\b",
        r"\bwho has\b",
    ]
    return any(re.search(t, q) for t in triggers)


# ----------------------------------------------------------------------
# Verifikacija
# ----------------------------------------------------------------------


def verify_result(
    question: str,
    columns: list[str],
    rows: list[list[Any]],
) -> tuple[bool, str | None]:
    """Provjeri je li rezultat sumnjiv s obzirom na pitanje.

    Returns:
        ``(is_suspicious, feedback)`` — ako True, feedback je human-readable
        poruka za retry prompt. Ako False, feedback je None.
    """

    row_count = len(rows)

    # Slučaj 1: očekuje COUNT (jedan broj), ali višestruki redovi
    if _question_expects_count(question):
        if row_count > 1:
            return (
                True,
                f"Your SQL returned {row_count} rows, but the question asks "
                f"'how many/count' which expects a SINGLE numeric answer. "
                f"Consider using COUNT(*) and avoiding GROUP BY (unless asked "
                f"for counts per group).",
            )
        if row_count == 1 and rows and len(rows[0]) > 1:
            return (
                True,
                f"Your SQL returned 1 row with {len(rows[0])} columns; the question "
                f"asks for a count so a single value is expected. Remove the extra "
                f"columns and return only the count.",
            )

    # Slučaj 2: očekuje listu, ali 0 redaka
    if _question_expects_list(question) and row_count == 0:
        return (
            True,
            "Your SQL returned 0 rows, but the question asks to LIST/SHOW results. "
            "Check your WHERE conditions — common causes: case-sensitive string "
            "mismatch (e.g., 'North Bohemia' vs 'north Bohemia'), too restrictive "
            "filters, or wrong join keys.",
        )

    # Slučaj 3: očekuje jednu vrijednost, ali višestruki redovi
    if _question_expects_single_value(question) and row_count > 1:
        # Ne flag-amo ako je samo "top N" varijanta single_value
        if not re.search(r"\btop \d+\b", question.lower()):
            return (
                True,
                f"Your SQL returned {row_count} rows; the question asks for "
                f"'the highest/lowest/maximum/minimum' which usually expects a "
                f"single row. Consider adding LIMIT 1 or using an aggregate "
                f"function (MAX/MIN).",
            )

    return False, None
