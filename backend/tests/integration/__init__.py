"""End-to-end integration testovi za FastAPI endpoint-e.

Filozofija: testiramo da handler + service + validator + (mock) executor
sjede zajedno kako treba. LLM je mockan — vraća fiksne SQL string-ove —
da testovi rade bez interneta i bez API ključeva.

Korisno za:
- Verifikaciju da blocked_reason path radi kraja-do-kraja
- Verifikaciju da je read-only enforcement vidljiv kroz API
- Sanity check JSON serijalizacije Pydantic modela
"""
