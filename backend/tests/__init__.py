"""Pytest test suite za NL2SQL backend.

Strukturiran u 4 sub-foldera:
- ``unit/``       — testovi pojedinačnih klasa/funkcija s mock ovisnostima
- ``validation/`` — sigurnosni testovi validatora (KRITIČAN za diplomski)
- ``integration/``— end-to-end /api/query test s mock LLM-om
- ``benchmark/``  — smoke test BIRD runner-a (3 pitanja, mock LLM)

Filozofija:
- Testovi koriste isti dialect default (postgres) kao production konfig.
- Schema u fixtures-ima je hard-coded Chinook subset — ne dohvaća se iz baze
  tako da unit/validation testovi rade bez Postgres ovisnosti.
- Integration testovi koriste FastAPI ``TestClient`` (uvicorn ne treba biti
  pokrenut) i mock LLM provider koji vraća fiksne SQL-ove.
"""
