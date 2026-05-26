"""Semantic checks — tablice i kolone moraju postojati u stvarnoj shemi.

Hvata tipičnu LLM halucinaciju: model izmisli tablicu ili kolonu koja
zvuči razumno ali ne postoji u bazi. Bez ovog sloja, izvršavanje bi palo
s kriptičnom DB greškom; s ovim slojem, RetryEngine dobije strukturirani
feedback "tablica X ne postoji, dostupne su Y, Z" i može popraviti SQL.

Dizajn-filozofija (po direktivi korisnika u planu Faze 2): **praktičan, ne
savršen**. Cilj prve iteracije:
- detektirati nepostojeće tablice ✅
- detektirati qualified kolone koje ne postoje ✅
- za unqualified kolone u JOIN-u: prihvatiti ako postoji u BAREM JEDNOJ
  korištenoj tablici (relaxed)

Edge case-ovi koji su izvan scope-a prve iteracije:
- WINDOW funkcije s OVER (alias resolution)
- LATERAL JOIN scoping
- Korelirani subquery-jevi (kolona iz outer scope-a)
- ARRAY/JSON path expressions

Ako test_validator.py pokaže false positives, postroživa se case-by-case.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from app.db.schema_inspector import DatabaseSchema, TableInfo


def check_semantics(ast: exp.Expression, schema: DatabaseSchema) -> list[str]:
    """Provjeri sve identifikatore u AST-u protiv stvarne sheme baze.

    Args:
        ast: AST iz ``parse_sql()``, već prošao safety_check.
        schema: dohvaćena shema baze.

    Returns:
        Lista poruka o nepostojećim tablicama / kolonama. Prazna = OK.
    """

    errors: list[str] = []

    # CTE imena su lokalno definirana s WITH alias AS (...) i ne postoje
    # u shemi. Mora ih se izuzeti iz "tablica ne postoji" provjere.
    cte_names = _collect_cte_names(ast)

    # SELECT aliasi (npr. `COUNT(*) AS album_count`) su lokalno definirani
    # i smiju se referencirati u ORDER BY / HAVING — PostgreSQL to dozvoljava.
    # Bez ovog skupa, validator bi pogrešno označio alias kao "kolona ne
    # postoji" pri referenci u ORDER BY.
    select_aliases = _collect_select_aliases(ast)

    # 1. Sve referencirane tablice + njihovi aliasi.
    table_refs = _collect_table_refs(ast, cte_names)

    # 2. Provjera postojanja tablica + razrjšenje aliasa u stvarne TableInfo.
    alias_to_table: dict[str, TableInfo] = {}
    available_tables_text = ", ".join(t.name for t in schema.tables)

    for table_name, alias in table_refs:
        table_info = schema.find_table(table_name)
        if table_info is None:
            errors.append(
                f"Tablica '{table_name}' ne postoji u shemi. "
                f"Dostupne tablice: {available_tables_text}."
            )
            continue
        # Alias je ono što LLM koristi u SELECT/WHERE klauzuli; ako alias
        # nije naveden, koristi se ime tablice. Mapping nam treba za
        # qualified kolone (a.name → resolves to artist).
        alias_to_table[(alias or table_name).lower()] = table_info

    # Ako su sve tablice nepostojeće, daljnja provjera kolona nema smisla.
    if not alias_to_table:
        return errors

    # 3. Sve referencirane kolone.
    for col_node in ast.find_all(exp.Column):
        col_name = col_node.name
        qualifier = col_node.table  # alias ili table name; "" ako nije qualified

        # Ako je ime jedan od SELECT aliasa, preskoči — to NIJE referenca na
        # stupac iz tablice nego na expression koji smo upravo izračunali.
        # Primjer: `COUNT(*) AS cnt ... ORDER BY cnt` — cnt je alias, ne stupac.
        if not qualifier and col_name.lower() in select_aliases:
            continue

        if qualifier:
            # Qualified: a.name → traži stvarnu tablicu kroz alias mapping.
            target = alias_to_table.get(qualifier.lower())
            if target is None:
                errors.append(
                    f"Kvalifikator '{qualifier}' u '{qualifier}.{col_name}' "
                    f"ne odgovara nijednoj tablici ni aliasu."
                )
                continue
            if not _table_has_column(target, col_name):
                cols_text = ", ".join(c.name for c in target.columns)
                errors.append(
                    f"Kolona '{col_name}' ne postoji u tablici '{target.name}'. "
                    f"Dostupne: {cols_text}."
                )
        else:
            # Unqualified: postoji li kolona u BAREM JEDNOJ korištenoj tablici?
            # Po dogovoru ova prva iteracija je relaxed (vidi file docstring).
            if not any(_table_has_column(t, col_name) for t in alias_to_table.values()):
                used_tables = ", ".join(t.name for t in alias_to_table.values())
                errors.append(
                    f"Kolona '{col_name}' ne postoji ni u jednoj korištenoj "
                    f"tablici ({used_tables})."
                )

    return errors


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _collect_cte_names(ast: exp.Expression) -> set[str]:
    """Vraća skup CTE alias imena (WITH alias AS (...)) — lowercase."""

    names: set[str] = set()
    for cte in ast.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            names.add(alias.lower())
    return names


def _collect_select_aliases(ast: exp.Expression) -> set[str]:
    """Vraća skup imena SELECT aliasa (`expr AS name`) — lowercase.

    Razlog postojanja: PostgreSQL dozvoljava referenciranje SELECT aliasa
    u ORDER BY i HAVING klauzulama. Validator mora znati za njih da ne
    bi pogrešno označio takav alias kao "kolona ne postoji".

    Prikupljamo aliase iz cijelog AST-a (uključujući subquery-jeve) jer
    je scope analiza kompleksnija; pretpostavka "ako je u nekom SELECT-u,
    smije se referencirati" je relaxed ali praktična za prvu iteraciju.
    """

    aliases: set[str] = set()
    for alias_node in ast.find_all(exp.Alias):
        # Alias node ima `.alias` ili `.alias_or_name` koji daje string ime.
        name = alias_node.alias_or_name
        if name:
            aliases.add(name.lower())
    return aliases


def _collect_table_refs(
    ast: exp.Expression, cte_names: set[str]
) -> list[tuple[str, str | None]]:
    """Vraća listu (table_name, alias) za sve eksterne tablice u AST-u.

    Tablice koje su zapravo CTE alias-i izuzimaju se jer one ne žive u shemi.
    """

    refs: list[tuple[str, str | None]] = []
    for table_node in ast.find_all(exp.Table):
        name = table_node.name
        if name.lower() in cte_names:
            # Ovo je referenca na CTE alias, ne na pravu tablicu — preskoči.
            continue
        # alias_or_name vraća alias ako postoji, inače ime tablice.
        # Razdvajamo da bismo znali kad je alias drugačiji od imena.
        alias = table_node.alias or None
        refs.append((name, alias))
    return refs


def _table_has_column(table: TableInfo, column_name: str) -> bool:
    """Case-insensitive provjera postojanja kolone u tablici."""

    target = column_name.lower()
    return any(c.name.lower() == target for c in table.columns)
