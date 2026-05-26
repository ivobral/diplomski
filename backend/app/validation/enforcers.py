"""Enforcers — automatske transformacije validnog SQL-a prije izvršavanja.

Glavna transformacija: dodavanje ``LIMIT`` na top-level SELECT ako ne
postoji. Cilj: spriječiti slučaj da LLM generira ``SELECT * FROM
huge_table`` koji vrati milijune redova i sruši backend. Read-only user
ne sprječava ovo na razini baze, ali ``LIMIT`` čini posljedice
ograničenima.

Pravilo: ne diramo subquery-je i CTE-ove — samo top-level. Razlog:
subquery-jevi često legitimno trebaju biti neograničeni (npr. ``SELECT
* FROM x WHERE id IN (SELECT id FROM y)``).
"""

from __future__ import annotations

from sqlglot import expressions as exp


def ensure_limit(ast: exp.Expression, default_limit: int) -> exp.Expression:
    """Dodaj ``LIMIT default_limit`` na top-level SELECT ako ga nema.

    Args:
        ast: validirani AST iz pipeline-a (Select ili With).
        default_limit: vrijednost iz ``settings.DEFAULT_LIMIT``.

    Returns:
        Modificirani AST (ili originalni ako LIMIT već postoji).
    """

    # WITH ... SELECT: ciljani SELECT je `ast.this` za With-node;
    # za plain SELECT, top-level je sam ast.
    target_select = _resolve_top_select(ast)
    if target_select is None:
        return ast  # nije SELECT (npr. blokiran je u safety_check; defensive)

    # Ako već postoji LIMIT na top-level SELECT-u, ne diramo.
    if target_select.args.get("limit") is not None:
        return ast

    # sqlglot pruža `.limit()` metodu koja vraća novi node s LIMIT-om.
    # Ona radi *u mjestu* za većinu builder-style operacija — provjereno
    # u sqlglot dokumentaciji za verziju 25+.
    return ast.limit(default_limit)


def normalize_sql(ast: exp.Expression, dialect: str = "postgres") -> str:
    """Pretty-print AST natrag u SQL string za izvršavanje i prikaz.

    Format je konzistentan (pretty=True), što olakšava:
    - debugging (lako pročitati u logovima i UI-u),
    - Exact Match metriku u Fazi 4 (eliminira whitespace varijacije).

    Args:
        ast: AST iz parse_sql.
        dialect: ``"postgres"`` ili ``"sqlite"`` — utječe na sintaksu
            specifičnih funkcija pri renderiranju natrag u SQL.
    """

    return ast.sql(dialect=dialect, pretty=True)


def _resolve_top_select(ast: exp.Expression) -> exp.Select | None:
    """Vraća top-level SELECT node, gdje god se nalazi.

    Slučajevi:
    - ``exp.Select`` — vraćamo isti node.
    - ``exp.With`` — top-level SELECT je ``ast.this`` (glavni SELECT
      nakon CTE definicija u WITH ... SELECT konstrukciji).
    """

    if isinstance(ast, exp.Select):
        return ast
    if isinstance(ast, exp.With) and isinstance(ast.this, exp.Select):
        return ast.this
    return None
