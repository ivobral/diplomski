"""Safety checks — drugi korak SQL validation pipeline-a (KRITIČAN SIGURNOSNI SLOJ).

Određuje smije li se SQL uopće izvršiti, neovisno o tome ima li smisla
semantički. Pravilo: dozvoljeno **isključivo** SELECT (i WITH ... SELECT).
Sve ostalo (DDL, DML, multi-statement, set operacije u prvoj iteraciji) je
blokirano.

Dizajn-pravilo: koristimo **allow-list** strategiju za korijenski node
(samo SELECT/WITH) i **deny-list** za bilo gdje u AST-u (čak i u CTE-u ne
smije se ušunjati DML). Ovo hvata exotic napade poput
``WITH x AS (DELETE FROM users RETURNING *) SELECT * FROM x`` — rijetko,
ali tehnički moguće u PostgreSQL-u.
"""

from __future__ import annotations

from sqlglot import expressions as exp

# ----------------------------------------------------------------------
# Allow-list i deny-list AST tipova
# ----------------------------------------------------------------------

# Korijenski node SQL upita MORA biti jedan od ovih:
_ALLOWED_ROOT_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.With,        # CTE: WITH ... SELECT ...
    exp.Union,       # SELECT ... UNION SELECT ...
    exp.Intersect,   # SELECT ... INTERSECT SELECT ...
    exp.Except,      # SELECT ... EXCEPT SELECT ...
)

# Bilo gdje u AST-u, ako se pojavi node ovih tipova → blok.
# Pokriva sve glavne DDL/DML kategorije + multi-statement (u sqlglot-u
# višestrukim statementima već smo bavili u ast_checks.py, ali "Command"
# pokriva exotic slučajeve poput VACUUM, ANALYZE itd. koji nisu SELECT).
_DENIED_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    # DML mutacije
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    # DDL
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    # PostgreSQL-specific i ostali nesigurni
    exp.Command,    # ANALYZE, VACUUM, COPY... — neselectivni
    exp.Transaction,  # BEGIN/COMMIT/ROLLBACK izlaze izvan jednog SELECT-a
)

# Set operacije (UNION/INTERSECT/EXCEPT) — dozvoljene od Faze 4 nadalje.
# Razlog odluke: failure analysis je pokazao da su to validni SELECT
# upiti koje LLM legitimno generira za "list X or Y" pitanja. Semantic
# check za UNION funkcionira jer sqlglot daje pristup objema stranama
# kroz `ast.find_all(exp.Column)`. Sve podzapite checkamo standardnim
# walk-om.
#
# Held intentionally as empty tuple (radije nego ukloniti varijablu) da
# regression test ne mora mijenjati shape — može se vratiti ako se ikad
# pokaže rizik.
_DENIED_SET_OPS: tuple[type[exp.Expression], ...] = ()


def check_safety(ast: exp.Expression) -> list[str]:
    """Pokreće sve safety provjere i vraća listu razloga blokiranja.

    Args:
        ast: AST korijenski node iz ``parse_sql()``.

    Returns:
        Lista poruka o blokiranju. Prazna lista znači "sve OK".
    """

    reasons: list[str] = []

    # Provjera 1: korijenski node je u allow-listi.
    # Ovo hvata 95% napada — korijenski DROP TABLE / DELETE / itd.
    if not isinstance(ast, _ALLOWED_ROOT_TYPES):
        reasons.append(
            f"Korijenski statement mora biti SELECT ili WITH. "
            f"Dobiveno: {type(ast).__name__}."
        )

    # Provjera 2: nigdje u AST stablu (uključujući CTE, subquery) ne smije
    # postojati DML/DDL node. Walk obilazi rekurzivno cijelo stablo.
    for node in ast.walk():
        # `walk()` u sqlglot-u >= 25 vraća direktno node-ove (ne tuple-ove
        # kao prije). Provjeravamo svaki node ako pripada deny-listi.
        if isinstance(node, _DENIED_NODE_TYPES):
            reasons.append(
                f"Operacija {type(node).__name__} nije dozvoljena "
                f"(samo SELECT statementi se mogu izvršavati)."
            )

    # Provjera 3: set operacije (UNION/INTERSECT/EXCEPT) — blok u prvoj iteraciji.
    for node in ast.walk():
        if isinstance(node, _DENIED_SET_OPS):
            reasons.append(
                f"Set operacija {type(node).__name__.upper()} privremeno "
                f"nije dozvoljena (re-evaluirati u Fazi 4 evaluacije)."
            )

    return reasons
