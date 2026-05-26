"""AST parsing — prvi korak SQL validation pipeline-a.

Pretvara raw SQL string u ``sqlglot`` AST. Ako parsing ne uspije ili
postoji više statementa (multi-statement attack: ``SELECT ...; DROP ...``),
podiže ``ValidationError``.

Razlog korištenja AST parsiranja umjesto regex-a: regex je trivijalno
zaobići (komentari, novi redovi, case manipulacija, Unicode whitespace).
AST je strukturalna analiza — sigurniji temelj za sve daljnje provjere.
"""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

from app.core.exceptions import ValidationError


def parse_sql(sql: str, dialect: str = "postgres") -> exp.Expression:
    """Parsa SQL i vraća jedinstveni AST node.

    Args:
        sql: SQL string (već očišćen od markdown blokova kroz ``extract_sql``).
        dialect: sqlglot dialect za parsing — ``"postgres"`` (default, demo)
            ili ``"sqlite"`` (BIRD benchmark). Default je postgres jer
            postojeći Chinook tijek koristi nju, pa pozivi bez argumenta
            zadržavaju staro ponašanje.

    Returns:
        Korijenski sqlglot Expression node (najčešće ``exp.Select`` ili
        ``exp.With``).

    Raises:
        ValidationError: ako parsing pada, ako je multi-statement, ili
            ako je rezultat prazan.
    """

    # sqlglot.parse vraća listu Expressiona — jedan po statementu (razdvojeno
    # po `;`). Multi-statement upit poput "SELECT 1; DROP TABLE x" daje listu
    # duljine 2 i mora biti odbijen kao potencijalni napad.
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except ParseError as exc:
        raise ValidationError(f"SQL parsing greška ({dialect}): {exc}") from exc

    # `sqlglot.parse` može vratiti `None` u listi za prazne statemente
    # (npr. trailing `;` koji nismo očistili). Filtriramo te elemente i
    # tretiramo kao 0 stvarnih statementa → fail.
    non_empty = [s for s in statements if s is not None]

    if len(non_empty) == 0:
        raise ValidationError("Prazan SQL nakon parsiranja.")
    if len(non_empty) > 1:
        raise ValidationError(
            f"Multi-statement upit nije dozvoljen (pronađeno {len(non_empty)} statementa)."
        )

    return non_empty[0]
