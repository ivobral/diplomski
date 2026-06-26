"""Smoke test za SQL Validator — direktan sigurnosni dokaz.

Skripta dokazuje sigurnost validatora **direktno**, ne preko LLM-a:
opasni SQL stringovi ulaze ravno u ``SqlValidator.validate()`` i moraju
biti odbijeni. NL-based testovi (npr. "Delete all artists" → /api/query)
korisni su kao demo, ali nisu dokaz — LLM može sam odbiti pitanje
i tada validator nikad ne bude pozvan.

Pokretanje (unutar backend kontejnera):

    docker compose exec backend python scripts/test_validator.py

Output: pass/fail po svakom test case-u. **100% pass je obavezan kriterij**
sigurnosti sustava (regression test).

Napomena: za production CI/test suite koristi ``pytest tests/validation/``
koji pokriva isti security set + dialect varijante.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from app.db.engine import get_main_engine
from app.db.schema_inspector import SchemaInspector
from app.validation.validator import SqlValidator

# ----------------------------------------------------------------------
# Test datasets
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """Jedan test case s opisom i SQL-om."""

    description: str
    sql: str


# Opasni SQL — validator MORA blokirati svaki (vraća blocked_reason).
# Ovo je glavni sigurnosni dokaz Faze 2 (direktivno u Q&A korisnika).
DANGEROUS_SQL: list[Case] = [
    Case("DELETE — DML mutacija",            "DELETE FROM artist"),
    Case("DROP TABLE — DDL",                 "DROP TABLE artist"),
    Case("Multi-statement (SQL injection)",  "SELECT * FROM artist; DROP TABLE artist"),
    Case("UPDATE — DML mutacija",            "UPDATE artist SET name = 'x'"),
    Case("TRUNCATE — DDL",                   "TRUNCATE TABLE artist"),
    Case("CREATE TABLE — DDL",               "CREATE TABLE evil (id int)"),
    Case("ALTER TABLE — DDL",                "ALTER TABLE artist ADD COLUMN evil text"),
    Case("INSERT — DML mutacija",            "INSERT INTO artist (name) VALUES ('x')"),
]


# Valjani SQL — validator MORA propustiti (ok=True, vraća normalized_sql).
VALID_SQL: list[Case] = [
    Case("Trivijalan SELECT *",
         "SELECT * FROM artist"),
    Case("JOIN + GROUP BY + aggregat",
         "SELECT a.name, COUNT(*) FROM artist a "
         "JOIN album b ON b.artist_id = a.artist_id GROUP BY a.name"),
    Case("WITH (CTE)",
         "WITH top_artists AS (SELECT artist_id FROM album GROUP BY artist_id) "
         "SELECT * FROM top_artists"),
    Case("Subquery u WHERE",
         "SELECT * FROM artist WHERE artist_id IN (SELECT artist_id FROM album)"),
    Case("PostgreSQL funkcija NOW()",
         "SELECT * FROM employee WHERE hire_date > NOW() - INTERVAL '20 years'"),
    Case("ILIKE PostgreSQL operator",
         "SELECT * FROM artist WHERE name ILIKE 'a%'"),
    Case("Već postojeći LIMIT",
         "SELECT * FROM artist LIMIT 5"),
    # Set operacije — dozvoljene od Faze 4 nadalje (validne SELECT kombinacije).
    Case("UNION (legitimni SELECT)",
         "SELECT name FROM artist UNION SELECT title FROM album"),
    Case("INTERSECT (legitimni SELECT)",
         "SELECT artist_id FROM artist INTERSECT SELECT artist_id FROM album"),
    Case("EXCEPT (legitimni SELECT)",
         "SELECT artist_id FROM artist EXCEPT SELECT artist_id FROM album"),
]


# Semantic fail — validator MORA označiti errors (ne blocked_reason; ovo
# je popravljivo retry-em u live tijeku).
SEMANTIC_FAIL: list[Case] = [
    Case("Nepostojeća tablica",  "SELECT * FROM nepostojeca_tablica"),
    Case("Nepostojeća kolona",   "SELECT pogresna_kolona FROM artist"),
]


# Auto-LIMIT — validator MORA dodati LIMIT na top-level SELECT bez njega.
AUTO_LIMIT_CASES: list[Case] = [
    Case("SELECT bez LIMIT",     "SELECT * FROM artist"),
]


# ----------------------------------------------------------------------
# Test runner
# ----------------------------------------------------------------------


async def run_tests() -> int:
    """Pokreni sve test setove, ispiši rezultate, vrati exit code (0 = pass)."""

    # Validator zahtjeva SchemaInspector — pravi protiv pravog DB-a (Chinook
    # je dostupan jer ovaj test radi u backend kontejneru s pristupom postgres-u).
    inspector = SchemaInspector(engine=get_main_engine())
    validator = SqlValidator(schema_inspector=inspector, default_limit=1000)

    total = 0
    passed = 0
    failures: list[str] = []

    # ----- Sigurnost: DANGEROUS_SQL → mora biti blocked --------------
    print("=" * 70)
    print("DANGEROUS SQL — moraju biti blokirani (blocked_reason set):")
    print("=" * 70)
    for case in DANGEROUS_SQL:
        total += 1
        result = await validator.validate(case.sql)
        is_blocked = result.blocked_reason is not None
        status = "PASS" if is_blocked else "FAIL"
        print(f"  [{status}] {case.description}")
        print(f"         SQL: {case.sql}")
        if is_blocked:
            print(f"         blocked_reason: {result.blocked_reason}")
            passed += 1
        else:
            failures.append(f"DANGEROUS NOT BLOCKED: {case.description} | {case.sql}")
            print("         !!! NIJE BLOKIRAN — ovo je sigurnosni propust !!!")

    # ----- Valjani SQL: VALID_SQL → mora biti ok=True ----------------
    print()
    print("=" * 70)
    print("VALID SQL — moraju proći (ok=True, normalized_sql set):")
    print("=" * 70)
    for case in VALID_SQL:
        total += 1
        result = await validator.validate(case.sql)
        status = "PASS" if result.ok else "FAIL"
        print(f"  [{status}] {case.description}")
        if result.ok:
            passed += 1
        else:
            failures.append(
                f"VALID REJECTED: {case.description} | "
                f"blocked={result.blocked_reason} errors={result.errors}"
            )
            print(f"         blocked_reason: {result.blocked_reason}")
            print(f"         errors: {result.errors}")

    # ----- Semantic fail: SEMANTIC_FAIL → ok=False, errors postoje ---
    print()
    print("=" * 70)
    print("SEMANTIC FAIL — moraju vratiti errors (ne blocked, ne ok):")
    print("=" * 70)
    for case in SEMANTIC_FAIL:
        total += 1
        result = await validator.validate(case.sql)
        is_semantic_fail = (not result.ok) and result.blocked_reason is None and bool(result.errors)
        status = "PASS" if is_semantic_fail else "FAIL"
        print(f"  [{status}] {case.description}")
        if is_semantic_fail:
            print(f"         errors: {result.errors[0]}")
            passed += 1
        else:
            failures.append(
                f"SEMANTIC MIS-LABELED: {case.description} | "
                f"ok={result.ok} blocked={result.blocked_reason}"
            )

    # ----- Auto-LIMIT: provjeri da je dodan kad nedostaje ------------
    print()
    print("=" * 70)
    print("AUTO-LIMIT — normalized_sql mora sadržavati LIMIT 1000:")
    print("=" * 70)
    for case in AUTO_LIMIT_CASES:
        total += 1
        result = await validator.validate(case.sql)
        has_limit = (
            result.ok
            and result.normalized_sql is not None
            and "LIMIT 1000" in result.normalized_sql.upper().replace("\n", " ")
        )
        status = "PASS" if has_limit else "FAIL"
        print(f"  [{status}] {case.description}")
        if has_limit:
            passed += 1
        else:
            failures.append(f"AUTO-LIMIT MISSING: {case.description}")
            print(f"         normalized_sql: {result.normalized_sql}")

    # ----- Sažetak ---------------------------------------------------
    print()
    print("=" * 70)
    print(f"REZULTAT: {passed} / {total} testova prošlo")
    print("=" * 70)
    if failures:
        print()
        print("NEUSPJESI:")
        for f in failures:
            print(f"  - {f}")
        print()
        print("REGRESSION: sigurnost validatora pala — popraviti i ponoviti.")
        return 1
    print()
    print("Validator je 100% pass — sigurnosni sloj zdrav.")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(run_tests())
    sys.exit(exit_code)
