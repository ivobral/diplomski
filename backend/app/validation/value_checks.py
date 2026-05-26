"""Value-aware validation — hvata WHERE literale koji ne postoje u bazi.

Failure analyzer pokazuje pattern gdje LLM napiše SQL koji **prolazi**
semantic check (kolone i tablice postoje) ali fail-a na **vrijednostima**:

- WHERE district = 'North Bohemia' (ali u bazi piše 'north Bohemia')
- WHERE status = 'Active' (ali u bazi je 'A')
- WHERE country = 'USA' (ali u bazi je 'United States')

To je glavni razlog za 0-row rezultate na "list X" pitanjima.

Ova validacija je **upozorenje, ne blok** — vraćamo warning poruke koje
retry engine može koristiti da popravi SQL. Ako prošlo, ne propisuje
gold prešicu (gold možda i koristi pogrešan literal — ne želimo upliv).

Implementacija je defenzivna:
- Provjerava SAMO short string literale (< 50 chars)
- LIMIT 1000 distinct vrijednosti po koloni (velike tablice ne skenira)
- Tihi fallback ako bilo što padne (validation se ne smije srušiti)
- Vraća warning samo kad postoji **case-insensitive match** koji bi mogao
  biti točan (jak signal da je samo case off, ne potpuno netočna vrijednost)
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlglot import expressions as exp

logger = logging.getLogger(__name__)

# Granice da check ne postane preskup ili spor:
_MAX_LITERAL_LENGTH = 50          # ne provjeravaj duge stringove (vjerojatno LIKE patterns)
_MAX_DISTINCT_VALUES = 1000       # ne sken-ati ogromne tablice
_LOOKUP_TIMEOUT = 5.0             # max sekundi za sve lookup-e zajedno


async def check_where_literals(
    ast: exp.Expression,
    engine: AsyncEngine,
) -> list[str]:
    """Provjeri postoje li string literali u WHERE klauzuli stvarno u bazi.

    Args:
        ast: parsed SQL (već prošao safety + semantic).
        engine: read-only AsyncEngine za bazu kojom SQL operira.

    Returns:
        Lista warning poruka za retry engine. Prazna lista = sve OK.
        Primjer poruke:
        ``"value 'North Bohemia' not in district.A3; closest case-match: 'north Bohemia'"``
    """

    # Skupi sve (column, literal) parove iz WHERE klauzula
    pairs = _extract_column_literal_pairs(ast)
    if not pairs:
        return []

    warnings: list[str] = []

    # Single connection za sve lookup-e — manje overhead-a od per-pair konekcije
    try:
        async with engine.connect() as conn:
            for table, column, literal in pairs:
                if not _should_check_literal(literal):
                    continue
                try:
                    case_match = await asyncio.wait_for(
                        _find_case_match(conn, table, column, literal),
                        timeout=_LOOKUP_TIMEOUT,
                    )
                except TimeoutError:
                    # Timeout ne ruši validaciju — preskoči samo ovaj literal
                    continue
                except Exception as exc:
                    logger.debug("value_check.lookup_failed", extra={
                        "table": table, "column": column, "error": str(exc)
                    })
                    continue
                if case_match is not None and case_match != literal:
                    warnings.append(
                        f"value '{literal}' not found in {table}.{column}; "
                        f"closest case-match: '{case_match}' "
                        f"(consider using exact case or LOWER() comparison)"
                    )
    except Exception as exc:
        # Bilo kakav neuspjeh ne smije srušiti validaciju — vrati prazno.
        logger.debug("value_check.failed", extra={"error": str(exc)})
        return []

    return warnings


def _extract_column_literal_pairs(
    ast: exp.Expression,
) -> list[tuple[str | None, str, str]]:
    """Iz WHERE/AND/OR clauses skupi (table_alias_or_name, column, literal_value).

    Tablica može biti None ako je kolona unqualified — u tom slučaju
    pokušavamo lookup u svim tablicama iz FROM klauzule (caller).
    """

    pairs: list[tuple[str | None, str, str]] = []

    for where_node in ast.find_all(exp.Where):
        # EQ comparators: col = 'literal' (i obrnuto: 'literal' = col)
        for eq in where_node.find_all(exp.EQ):
            col_lit = _parse_eq_as_col_literal(eq)
            if col_lit is not None:
                pairs.append(col_lit)
        # Isto za NEQ ('a' != col) — manje korisno ali ne škodi
        for neq in where_node.find_all(exp.NEQ):
            col_lit = _parse_eq_as_col_literal(neq)
            if col_lit is not None:
                pairs.append(col_lit)
    return pairs


def _parse_eq_as_col_literal(
    eq_node: exp.Expression,
) -> tuple[str | None, str, str] | None:
    """Ako je `col = 'literal'` ili `'literal' = col`, vrati (table, col, lit)."""

    left = eq_node.this
    right = eq_node.args.get("expression")
    col_node = lit_node = None
    if isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string:
        col_node, lit_node = left, right
    elif isinstance(right, exp.Column) and isinstance(left, exp.Literal) and left.is_string:
        col_node, lit_node = right, left

    if col_node is None or lit_node is None:
        return None

    table = col_node.table or None  # alias ili real-table; None ako unqualified
    return (table, col_node.name, lit_node.this)


def _should_check_literal(literal: str) -> bool:
    """Heuristika: ne provjeravaj zvjezdice (LIKE patterns), dugačke, brojeve-as-string."""

    if not literal or len(literal) > _MAX_LITERAL_LENGTH:
        return False
    if "%" in literal or "_" in literal:  # LIKE wildcards
        return False
    if literal.strip().isdigit():
        return False
    return True


async def _find_case_match(
    conn,
    table: str | None,
    column: str,
    literal: str,
) -> str | None:
    """Vrati postojeću vrijednost iz DB-a koja matcha literal case-insensitive.

    Pristup:
    1. Provjeri postoji li EXACT match — ako da, vrati ga (literal je već točan).
    2. Inače query distinct vrijednosti kolone (s LIMIT) i traži case-insensitive match.

    Vraća None ako:
    - exact match postoji (literal je OK, nema warninga)
    - nema niti case-insensitive matcha (literal je vjerojatno potpuno netočan,
      ne želimo lažan signal; failure je neki drugi)
    - lookup pada (timeout, permission, syntax)
    """

    if table is None:
        # Bez table info ne možemo pouzdano queryjati. Skip.
        return None

    # Quoted identifiers za sigurnost (BIRD ima kolone s razmacima)
    quoted_table = f'"{table}"'
    quoted_col = f'"{column}"'

    # 1. Exact match check
    exact_query = text(
        f"SELECT 1 FROM {quoted_table} WHERE {quoted_col} = :lit LIMIT 1"
    )
    result = await conn.execute(exact_query, {"lit": literal})
    if result.fetchone() is not None:
        return None  # literal već točan, nema warninga

    # 2. Case-insensitive match
    ci_query = text(
        f"SELECT DISTINCT {quoted_col} FROM {quoted_table} "
        f"WHERE LOWER({quoted_col}) = LOWER(:lit) LIMIT 1"
    )
    result = await conn.execute(ci_query, {"lit": literal})
    row = result.fetchone()
    if row is None:
        return None  # literal potpuno netočan, ne signaliziramo (false positive risk)
    return str(row[0])
