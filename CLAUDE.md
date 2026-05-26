# NL2SQL — Sustav za generiranje SQL upita iz prirodnog jezika

> Ovo je glavni specifikacijski i memo file projekta. Učitava se automatski u svakoj Claude Code sesiji u ovom direktoriju.

## Opis projekta

Sustav koji korisniku omogućuje postavljanje upita nad relacijskom bazom podataka koristeći prirodni jezik. Sustav:

- dinamički dohvaća shemu baze (bez hardcodiranja),
- konstruira kvalitetan prompt,
- koristi veliki jezični model (LLM) za generiranje SQL upita,
- validira generirani SQL (sigurnost + semantika),
- sigurno izvršava SQL nad bazom (read-only user, timeout),
- prikazuje rezultat korisniku,
- omogućuje benchmark evaluaciju točnosti, robusnosti i performansi.

Projekt se razvija kao **diplomski rad** — implementacija mora biti profesionalna, modularna, čitljiva, edukativno komentirana, i istovremeno izgledati kao ozbiljan istraživački sustav.

---

## Anti-pravila (najvažnije — NE raditi)

- **NE koristiti LangChain, LlamaIndex** ili slične wrapper-frameworke. Sakrivaju logiku, teže su objašnjivi u radu, agentske apstrakcije nestabilne. Koristiti izravne SDK-ove (`anthropic`, `openai`, `httpx` za Ollama).
- **NE overengineerati**. Tri slične linije > prerana apstrakcija. Pattern uvoditi tek kad postoji 3+ konkretne varijacije.
- **NE pisati trivijalne komentare**. Komentari objašnjavaju **ZAŠTO**, ne ŠTO. Ne pretrpavati kod šumom.
- **NE production-grade DevOps**: ne GitHub Actions, ne Kubernetes, ne reverse proxy, ne multi-stage produkcijski Dockerfile-ovi. Diplomski se boduje na evaluaciji/sigurnosti/arhitekturi, ne na DevOps-u.
- **NE multi-provider UI** u prvoj iteraciji — provider se bira preko env varijable.
- **NE hardcoded shema** — sve dinamično iz `SchemaInspector`.
- **NE magic stringovi** — konstante u `config.py` ili module-level.

---

## Faze izgradnje

Svaka faza MORA biti potpuno funkcionalna prije sljedeće.

| Faza | Sadržaj | Status |
|------|---------|--------|
| 1 | Skeleton: docker compose up + /api/health + /api/schema | u tijeku |
| 2 | Backend core: SchemaInspector, PromptBuilder, LLM provideri, SqlValidator, QueryExecutor, RetryEngine, /api/query end-to-end | pending |
| 3 | Frontend: Next.js UI s pitanjem → SQL → rezultatom | pending |
| 4 | Evaluacija: BIRD-Mini benchmark, eksperimenti A/B/C/D, metrike | pending |
| 5 | README + pytest testovi + polish | pending |

---

## Tehnologije

### Backend
- Python 3.12+ (`backend/Dockerfile` koristi `python:3.12-slim`)
- FastAPI + uvicorn
- SQLAlchemy 2.x (async) + asyncpg
- Pydantic v2 + pydantic-settings
- sqlglot (SQL parsing/validation)
- anthropic, openai, httpx (LLM provideri)
- structlog
- uv (package manager)

### Frontend
- Next.js 14+ (App Router)
- React + TypeScript
- TailwindCSS

### Database
- PostgreSQL 16 (u Docker containeru)
- Glavna demo baza: **Chinook**

### Benchmark
- **BIRD-Mini** dataset (download skripta u Fazi 4)

---

## Arhitektura

```
Next.js UI (frontend)
       │ HTTP (JSON)
       ▼
FastAPI (backend)
 │
 ├─ /api/query
 │    SchemaInspector → PromptBuilder → LLMProvider
 │    → SqlValidator → (retry on fail) → QueryExecutor → response
 │
 ├─ /api/schema   (dohvat sheme)
 ├─ /api/health   (zdravlje sustava)
 └─ /api/evaluate (benchmark, Faza 4)
       │
       ▼
PostgreSQL (Chinook + BIRD-Mini)
```

---

## Folder struktura

```
dipl/
├── CLAUDE.md
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
│
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── app/
│       ├── main.py
│       ├── config.py
│       ├── api/          # FastAPI routeri
│       ├── core/         # logging, exceptions, timing
│       ├── db/           # engine, schema_inspector
│       ├── llm/          # provideri (anthropic/openai/ollama) + prompts/
│       ├── validation/   # sqlglot AST + safety + semantic + enforcers
│       ├── services/     # query_service, retry_engine, execution_service
│       ├── evaluation/   # benchmark (Faza 4)
│       ├── models/       # Pydantic DTO sheme
│       └── utils/
│
├── frontend/             # Next.js + TS + Tailwind (Faza 3)
│
├── data/
│   ├── chinook/          # PostgreSQL dump
│   └── bird_mini/        # benchmark dataset (Faza 4)
│
├── scripts/
│   ├── create_readonly_user.sql
│   └── seed_chinook.sh
│
└── tests/                # pytest (Faza 5)
    ├── unit/
    ├── integration/
    ├── validation/
    └── benchmark/
```

---

## Glavni moduli (sažetak)

### Schema Inspector (`db/schema_inspector.py`)
Dinamički dohvaća tablice, kolone, tipove, PK, FK, relacije preko SQLAlchemy `inspect()` + `information_schema`. Rezultat keširan in-memory (TTL configurable).

### Prompt Builder (`llm/prompts/builder.py`)
Konstruira prompt na temelju **strategije** (eksperiment A/B/C/D). Sadrži: pitanje, shemu, relacije, sigurnosna pravila.

### LLM Provider Layer (`llm/`)
- `BaseLLMProvider` (ABC) → `AnthropicProvider`, `OpenAIProvider`, `OllamaProvider`
- Factory bira providera na temelju `settings.LLM_PROVIDER` env varijable.
- Default modeli: `claude-opus-4-7` (Anthropic), `gpt-4o` (OpenAI), `sqlcoder` (Ollama).

### SQL Validation (`validation/`) — KRITIČNO
Pipeline:
1. **AST parsing** (`ast_checks.py`) — `sqlglot.parse(sql, read="postgres")`, točno 1 statement.
2. **Safety checks** (`safety_checks.py`) — samo SELECT dozvoljen; blokirati INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE i multi-statement.
3. **Semantic checks** (`semantic_checks.py`) — sve tablice i kolone moraju postojati u shemi.
4. **Enforcers** (`enforcers.py`) — ako `LIMIT` ne postoji, dodaj `LIMIT 1000`.
5. Vraća `ValidationResult(ok, errors, normalized_sql)`.

### Query Execution (`services/execution_service.py`)
Koristi `READONLY_DATABASE_URL` (read-only user), `asyncio.wait_for(..., timeout)`, vraća rows + columns + ms.

### Retry & Self-Correction (`services/retry_engine.py`)
Ako validacija fail-a, šalje LLM-u poseban prompt s greškom + dostupnim kolonama. Max `MAX_RETRY_ATTEMPTS` (default 2).

---

## Sigurnost (kritičan dio diplomskog rada)

Sustav MORA blokirati sljedeće primjere:
- `"DELETE all users"` → odbijeno (samo SELECT)
- `"DROP TABLE customers"` → odbijeno
- `"Show all users; DROP TABLE users"` → odbijeno (multi-statement)
- `"Remove all orders"` → odbijeno

Slojevi obrane:
1. **Prompt** — system prompt eksplicitno traži samo SELECT.
2. **SqlValidator** — AST-based provjera (ne regex). Sigurnost se ne smije oslanjati samo na prompt.
3. **Read-only DB user** — `nl2sql_readonly` ima samo `GRANT SELECT`.
4. **Timeout** — `QUERY_TIMEOUT_SECONDS` sprječava dugotrajne upite.
5. **Auto LIMIT** — sprječava case-ove tipa "SELECT * FROM huge_table" bez ograničenja.

---

## Evaluacija (Faza 4 — najvažniji dio za diplomski)

### Eksperimenti
- **A**: prompt = samo pitanje
- **B**: prompt = pitanje + shema
- **C**: prompt = pitanje + shema + relacije + sigurnosna pravila
- **D**: prompt = pitanje + shema + relacije + validacija s retry mehanizmom

### Metrike
- **Exact Match** — string match nakon `sqlglot` normalizacije
- **Execution Accuracy** — set equality nad result row-ovima
- **Validation Success Rate** — koliko SQL upita prođe validaciju
- **Error Rate** — koliko upita failuje
- **Security Rejection Rate** — koliko opasnih upita je blokirano
- **Latency breakdown** — LLM ms / validation ms / execution ms / total ms
- **Token Usage** — gdje provider podržava

### Robustness test setovi
- filtriranje, agregacije, single JOIN, multi JOIN, GROUP BY, HAVING, date filter
- dvosmislena pitanja, nepostojeće kolone
- SQL injection pokušaji, zabranjene operacije

---

## Stil koda (pravila koja se prate cijelim projektom)

- Header docstring na svakom file-u: što radi, odgovornost, mjesto u arhitekturi.
- Docstring (Google style) na svakoj javnoj funkciji i klasi: što radi, Args, Returns, Raises, zašto postoji.
- Komentari = ZAŠTO ne ŠTO. Ne komentirati očite linije.
- Type hints svuda (Python 3.12 syntax: `list[str]`, `str | None`).
- Pydantic v2 za sve DTO-e.
- Async za I/O (DB, HTTP, LLM). Sync za CPU-bound (validator, prompt builder).
- Dependency injection kroz FastAPI `Depends()` — bez DI containera.
- Bez magic stringova — konstante u `config.py` ili module-level.
- Bez hardcoded sheme — sve iz `SchemaInspector`.
- Structured logging (structlog) — ključni eventi s contextom.

---

## Pokretanje (Faza 1)

```powershell
cd C:\Users\Korisnik\dipl
cp .env.example .env
# uredi .env (API ključ za Anthropic ili OpenAI)
docker compose up --build
```

Smoke testovi:
- `curl http://localhost:8000/api/health` → `{"status":"ok"}`
- `curl http://localhost:8000/api/schema` → JSON s tablicama Chinook baze
- `http://localhost:8000/docs` → Swagger UI
- `http://localhost:3000` → frontend placeholder
