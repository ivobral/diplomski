"""Use case orkestracija — services layer.

Servisi spajaju komponente (SchemaInspector, LLMProvider, SqlValidator,
QueryExecutor, RetryEngine) u kompletne tijekove rada:

- ``query_service``     — end-to-end: pitanje → SQL → validacija → izvršavanje.
- ``retry_engine``      — self-correction kada validacija ne prođe.
- ``execution_service`` — sigurno izvršavanje SQL-a preko readonly engine-a.

API routeri (``app/api/``) trebaju zvati servise, ne biti debeli sami po sebi.

Implementacija dolazi u Fazi 2.
"""
