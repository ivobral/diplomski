"""Pydantic v2 DTO sheme — request/response objekti za API.

Sve što ulazi i izlazi iz API-ja prolazi kroz Pydantic modele iz ovog
paketa. Razlog je dvostruk:

1. **Validacija ulaza** — neispravan request automatski vraća HTTP 422
   s preciznim opisom problema (Pydantic se brine).
2. **Sigurna serijalizacija izlaza** — ne curi nikakav unutarnji state
   koji nije eksplicitno deklariran u response modelu.

Razdvojeno od internih dataclass-ova u ``app/db/`` namjerno: API DTO
mijenja se na drugačijem tempu od interne reprezentacije i mora imati
stabilan oblik (kompatibilnost s frontend-om i mentor-om koji čita rad).
"""
