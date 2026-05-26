"""Sigurnosni testovi validatora — KRITIČAN dio test suite-a za diplomski.

Validator je glavni sigurnosni sloj sustava (uz read-only DB usera). Ovi
testovi direktno provjeravaju da:
- Sve DDL/DML operacije su blokirane (DROP, DELETE, INSERT, UPDATE, ALTER,
  CREATE, TRUNCATE) — moraju vratiti ``blocked_reason``.
- Multi-statement upiti (SQL injection vektor) su blokirani.
- Validni SELECT-ovi prolaze (single, JOIN, CTE, subquery, UNION).
- Semantic check hvata krive identifikatore (nepostojeća tablica/kolona).
- Auto-LIMIT se dodaje na top-level SELECT bez explicitnog LIMIT-a.

Filozofija: testovi su **direktni** — SQL string ide u validator, asertujemo
output. Ne testira se preko LLM-a (LLM može slučajno odbiti opasan upit,
što ne dokazuje sigurnost validatora). Ovo je standard za "defense in depth"
testiranje.
"""
