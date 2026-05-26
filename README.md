# NL2SQL — Sustav za generiranje SQL upita iz prirodnog jezika

Diplomski rad. Sustav koji koristi velike jezične modele (LLM) za pretvorbu pitanja na prirodnom jeziku u sigurne SQL upite nad relacijskom bazom podataka.

**Glavni rezultat:** **47% Execution Accuracy** na BIRD Mini-Dev benchmarku (200 pitanja) s GPT-4o-mini modelom i originalnom *Cascade v2* arhitekturom — uz **98.5% validation success** i **0% propusta opasnih SQL upita** (DROP/DELETE/INSERT/...) kroz validator.

---

## Sadržaj

1. [Sažetak](#1-sa%C5%BEetak)
2. [Motivacija](#2-motivacija)
3. [Arhitektura](#3-arhitektura)
4. [Sigurnosni sloj](#4-sigurnosni-sloj-kriti%C4%8Dan)
5. [LLM provideri](#5-llm-provideri)
6. [Eksperimentalna metodologija](#6-eksperimentalna-metodologija)
7. [Rezultati](#7-rezultati)
8. [Glavni doprinosi](#8-glavni-doprinosi)
9. [Folder struktura](#9-folder-struktura)
10. [Pokretanje](#10-pokretanje)
11. [Testovi](#11-testovi)
12. [Reference iz literature](#12-reference-iz-literature)
13. [Ograničenja i budući rad](#13-ograni%C4%8Denja-i-budu%C4%87i-rad)

---

## 1. Sažetak

Sustav omogućuje korisniku da postavi pitanje na hrvatskom ili engleskom (npr. *"Koliko izvođača ima u bazi?"*) i dobije:
- generirani SQL upit (vidljiv, ne skriven),
- rezultate izvršavanja nad bazom,
- mjerenja po fazama (LLM, validacija, izvršavanje),
- status validacije i razlog blokiranja ako se radilo o opasnom upitu.

Sustav je modularan: 4 LLM providera (Anthropic, OpenAI, Gemini, Ollama) zamjenjivih kroz `.env`, dialekt-aware validator (PostgreSQL za Chinook demo, SQLite za BIRD benchmark), te potpuna arhitektura strategija prompta (A/B/C/D) i pipeline poboljšanja (Cascade).

**Glavni inženjerski doprinos**: dokazana je hipoteza da **naivno OR-iranje "modernih LLM tehnika" ne poboljšava točnost**, a često je **gore od baseline-a**. *Cascade arhitektura* — uvjetna aktivacija poboljšanja po failure mode-u — rješava interferenciju i daje **+4–7 pp Execution Accuracy** uz manje računskog troška od naivnog pristupa.

---

## 2. Motivacija

### Zašto NL2SQL

- **Business intelligence**: ne-tehnički korisnici žele postaviti pitanja podacima bez učenja SQL-a.
- **Ubrzanje analize**: čak i SQL eksperti gube vrijeme na rutinski upite (broj redaka, grupiranja, joinevi).
- **Pristup novim bazama**: kad otvarate novu bazu, ne morate učiti njenu shemu — LLM je razumije ako mu ju pokažete.

### Tri ključna izazova

1. **Razumijevanje pitanja**: LLM mora znati koje tablice/kolone su relevantne. Naivni pristup (LLM bez konteksta) daje **1% točnosti**.
2. **Sigurnost**: LLM bez ograničenja može generirati `DROP TABLE` ili `DELETE FROM` na korisnikov upit *"obriši stare narudžbe"*. Ovaj sustav mora **100% blokirati** takve upite.
3. **Evaluacija**: "izgleda razumno" nije mjerljivo. Trebamo standard benchmark (BIRD) i jasne metrike.

---

## 3. Arhitektura

### High-level dijagram

```
┌──────────────────────────┐
│  Next.js UI (frontend)   │
│  /  → glavni demo        │
│  /benchmark → ablation   │
└──────────────┬───────────┘
               │ HTTP (JSON)
               ▼
┌────────────────────────────────────────────────────────────────┐
│  FastAPI backend                                               │
│                                                                │
│  POST /api/query                                               │
│   │                                                            │
│   ├──▶ SchemaInspector ──── (cache) ── SQLAlchemy ──▶ Postgres │
│   │                                                            │
│   ├──▶ PromptBuilder  ←── strategija (A/B/C/D)                 │
│   │     │                                                      │
│   ├──▶ LLMProvider (Anthropic | OpenAI | Gemini | Ollama)      │
│   │     │                                                      │
│   ├──▶ SqlValidator (sqlglot AST + semantic + safety + dialect)│
│   │     │                                                      │
│   │     └──▶ ako invalid → RetryEngine → LLM (popravi)         │
│   │                                                            │
│   └──▶ QueryExecutor (read-only user, 60s timeout)             │
│                                                                │
│  POST /api/evaluate                                            │
│   └──▶ BenchmarkRunner → BenchmarkQueryService                 │
│         (cascade, ablation, BIRD-Mini SQLite baze)             │
└──────────────────────┬─────────────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
┌──────────────────┐         ┌──────────────────┐
│  PostgreSQL      │         │  SQLite (×11)    │
│  (Chinook demo)  │         │  (BIRD Mini-Dev) │
└──────────────────┘         └──────────────────┘
```

### Tijek za jedno pitanje (strategija D + Cascade)

1. **Schema Inspector** dohvati shemu trenutne baze (cached u memoriji s TTL-om).
2. **Schema Linking** (LLM call) — koje tablice su relevantne za ovo pitanje?
3. **Decomposition** (LLM call) — razloži pitanje u korake (filtriraj → grupiraj → agregiraj).
4. **Prompt Builder** sastavi sistem + user prompt s odgovarajućim sadržajem prema strategiji.
5. **LLM provider** generira SQL (glavni LLM call).
6. **SqlValidator** parsira SQL kroz sqlglot, provjerava:
   - parsing (točno 1 statement),
   - safety (samo SELECT, bez DDL/DML),
   - semantics (sve tablice/kolone postoje),
   - enforce auto-LIMIT 1000 (osim u benchmark mode-u).
7. **Ako validation fail** (D strategija): RetryEngine šalje LLM-u feedback poruku s prošlim SQL-om + greškama + dostupnim identifikatorima. Max 2 retry-a.
8. **Cascade Layer 2** (opcionalno): ako validation i dalje fail, ponovo s column-linking (uži schema).
9. **QueryExecutor** izvrši kroz read-only DB usera s asyncio timeout.
10. **Cascade Layer 3** (opcionalno): ako rezultat izgleda sumnjivo (0 rows na "list", multi-row na "count"), pokrene se self-consistency (N=5 paralelnih LLM poziva, glasanje po rezultatu).
11. Vraća se JSON s SQL-om, rezultatima, latency breakdown-om i statusom.

### Glavni moduli (mapping na folder strukturu)

| Modul | Folder | Što radi |
|---|---|---|
| Schema Inspector | `app/db/schema_inspector.py` | dohvat tablica/kolona/FK preko SQLAlchemy + cache |
| Prompt Builder | `app/llm/prompts/` | strategije A/B/C/D, retry prompt, schema linking |
| LLM Provider | `app/llm/` | abstrakcija + 4 implementacije |
| SqlValidator | `app/validation/` | sqlglot AST + safety + semantic + enforcers |
| Query Service | `app/services/query_service.py` | end-to-end orkestracija za /api/query |
| Benchmark Service | `app/services/benchmark_query_service.py` | BIRD tijek s Cascade arhitekturom |
| Result Judge | `app/services/result_judge.py` | LLM-as-judge za Cascade v3 |
| Few-shot Retriever | `app/evaluation/few_shot_retrieval.py` | TF-IDF retrieval (DAIL-SQL) |
| Failure Analyzer | `app/evaluation/failure_analyzer.py` | AST diff klasifikacija grešaka |

---

## 4. Sigurnosni sloj (KRITIČAN)

Sustav koristi **5 obrambenih slojeva** (defense in depth):

### Sloj 1: Prompt
Sistem prompt eksplicitno traži: *"Generiraj isključivo SELECT upite. Nikad ne piši INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE."* Ovo je **najslabiji** sloj — LLM-ovi povremeno ignoriraju upute, ali postoje ovaj sloj filtrira većinu trivijalnih slučajeva.

### Sloj 2: Validator (sqlglot AST) — **glavna obrana**
- Parsira generirani SQL kroz `sqlglot.parse(sql, read=dialect)`.
- Provjerava da je root statement `SELECT` ili `WITH` (CTE).
- Walka cijelo AST stablo i blokira bilo koji `Insert/Update/Delete/Drop/Alter/Create/TruncateTable` node — čak i unutar CTE-a.
- Blokira multi-statement (`;` razdvajanje, klasični SQL injection vektor).
- Provjerava da sve referencirane tablice i kolone postoje u shemi.
- Dodaje `LIMIT 1000` na top-level SELECT ako nema eksplicitnog (sprječava `SELECT * FROM huge_table`).

### Sloj 3: Read-only DB user
- PostgreSQL `nl2sql_readonly` korisnik kreiran s `GRANT SELECT` samo na `public` shemu.
- Čak i ako validator nekako propusti opasan SQL, DB ga odbije.
- Konekcija: `READONLY_DATABASE_URL` (zaseban env varijabla od glavnog DB usera).

### Sloj 4: Timeout
- `asyncio.wait_for(...)` s 60-sekundnim cap-om na svako izvršavanje.
- Sprječava DoS preko `SELECT pg_sleep(99999)`.

### Sloj 5: Auto-LIMIT
- Default `LIMIT 1000` se automatski dodaje na top-level SELECT-ove bez LIMIT-a.
- Sprječava `SELECT * FROM huge_table` koji bi mogao iscrpiti memoriju.

### Primjeri blokiranih upita (sve iz pytest validation/ suite-a)

| SQL | Razlog blokiranja |
|---|---|
| `DELETE FROM artist` | "Korijenski statement mora biti SELECT ili WITH. Dobiveno: Delete" |
| `DROP TABLE artist` | "Operacija Drop nije dozvoljena" |
| `SELECT * FROM artist; DROP TABLE artist` | "Multi-statement upit nije dozvoljen (pronađeno 2 statementa)" |
| `UPDATE artist SET name = 'x'` | "Operacija Update nije dozvoljena" |
| `TRUNCATE TABLE artist` | "Operacija TruncateTable nije dozvoljena" |
| `CREATE TABLE evil (id int)` | "Operacija Create nije dozvoljena" |
| `INSERT INTO artist (name) VALUES ('x')` | "Operacija Insert nije dozvoljena" |

**Sve gore navedeno mora vratiti `blocked=True` u 100% slučajeva** — ovo je dokazano kroz `tests/validation/test_security.py` (26 testova, 100% pass).

---

## 5. LLM provideri

Sustav podržava **četiri** providera koji se mijenjaju kroz `.env` bez koda restarta:

| Provider | API | Best Practice | Pricing (input/output per 1M tok) |
|---|---|---|---|
| **OpenAI** | `openai` SDK | `gpt-4o-mini` za benchmark (cheap), `gpt-5-mini` za reasoning | $0.15 / $0.60 (4o-mini) |
| **Anthropic** | `anthropic` SDK | `claude-haiku-4-5` za jeftino, `claude-sonnet-4-5` za top | $1 / $5 (haiku) |
| **Google Gemini** | direktan REST kroz `httpx` | `gemini-2.5-flash` (free tier!) | besplatno do 1500 req/dan |
| **Ollama** | lokalni HTTP server | `sqlcoder`, `qwen2.5-coder:7b` | besplatno, lokalno |

### Kako odabrati

```env
# .env
LLM_PROVIDER=openai      # ili: anthropic | gemini | ollama
OPENAI_MODEL=gpt-4o-mini # naziv ovisi o provideru (vidi .env.example)
OPENAI_API_KEY=sk-...
```

### Kako dodati novi provider

1. Stvori `app/llm/myprovider.py` koji nasljeđuje `BaseLLMProvider`.
2. Implementiraj `async generate(prompt) -> LLMResponse` i `def name()`.
3. Dodaj case u `app/llm/factory.py` `create_llm_provider_for()`.
4. Dodaj env varijable u `app/config.py` i `.env.example`.

Cijela LLM apstrakcija je 200 linija koda — namjerno **minimalna** i **bez wrapper biblioteka** (nema LangChain, LlamaIndex). Razlog: u diplomskom radu se mora demonstrirati eksplicitna kontrola nad prompt-om/validacijom, ne wrapper-nad-wrapperom apstrakcija.

---

## 6. Eksperimentalna metodologija

### Dataset: BIRD Mini-Dev

[BIRD (Big Bench for Large-scale Database Grounded Text-to-SQL Evaluation)](https://bird-bench.github.io/) — standard u literaturi za NL2SQL benchmark.

- **Mini-Dev verzija**: 500 pitanja preko 11 SQLite baza (`california_schools`, `card_games`, `codebase_community`, `debit_card_specializing`, `european_football_2`, `financial`, `formula_1`, `student_club`, `superhero`, `thrombosis_prediction`, `toxicology`).
- Svako pitanje sadrži: `question` (engleski tekst), `gold_sql` (točan SQL), `evidence` (expert hint), `difficulty` (`simple` / `moderate` / `challenging`), `db_id`.
- Za naš run: **200 pitanja** (prvi N po `question_id`) — dovoljno za stabilan ±3pp signal.

### Strategije prompta (A/B/C/D)

| Strategija | Što sadrži user prompt | Cilj |
|---|---|---|
| **A** | Samo pitanje | Naivni baseline — pokazuje koliko LLM "zna sam" |
| **B** | Pitanje + schema (tablice + kolone + tipovi) | Mjera utjecaja schema visibility |
| **C** | B + foreign key relacije + sigurnosna pravila | Mjera utjecaja relations (joinevi) |
| **D** | C + schema linking + decomposition + sample rows + BIRD descriptions + evidence + retry + execute-then-verify retry | Puni production pipeline |

### Cascade arhitektura (originalni doprinos)

D-basic uvijek pokreće cijeli pipeline. **Cascade** je adaptivna nadgradnja koja uključuje dodatna poboljšanja **samo kad treba** (po failure mode-u):

- **Layer 1**: D-basic + (opcionalno) few-shot. Pokriva ~94% pitanja.
- **Layer 2**: ako validacija fail → ponovni run s **column-linking** (uži schema). Recovers ~85% Layer 1 fail-ova.
- **Layer 3**: ako rezultat sumnjiv (`verify_result` heuristika ili LLM-as-Judge) → ponovni run sa **self-consistency** (N=5 paralelnih LLM, glasanje po rezultatu).

### Metrike

| Metrika | Definicija |
|---|---|
| **Execution Accuracy (EX)** | Vraćaju li generirani i gold SQL **iste rezultate** (set-equality, numeric tolerance ±1e-6) |
| **Exact Match (EM)** | String match nakon sqlglot normalizacije (često prestrog jer ne dozvoljava semantički ekvivalentne varijacije) |
| **Validation Success Rate** | Postotak generiranih SQL-ova koji prolaze validator (parse + safety + semantic) |
| **Blocked Rate** | Postotak koji je `blocked_reason` (sigurnosni cilj: 100% na opasnim upitima, 0% na legitimnima) |
| **Latency Breakdown** | LLM ms / validation ms / execution ms / total ms |
| **Token Usage** | Input + output tokena, za cost analizu |

---

## 7. Rezultati

### Glavna ablation tablica (200 pitanja, gpt-4o-mini)

| Strategija | Komponente | EX | Validation | Cost (200Q) |
|---|---|---|---|---|
| **A** | samo pitanje | **0%** | 9.5% | $0.03 |
| **B** | + schema | 16.5% | 91% | $0.05 |
| **C** | + relations + safety | 17.5% | 91.5% | $0.05 |
| **D-basic** | + schema linking + decomposition + sample rows + descriptions + evidence + retry + verify-then-retry | **43%** | 94.5% | $0.19 |
| **D + Cascade v2** ⭐ | D + uvjetna aktivacija (column-linking + self-consistency) | **47%** | **98.5%** | $0.25 |

### Najvažniji deltaovi

- **A → B = +16.5pp**: schema visibility je kritična
- **B → C = +1pp**: dodavanje relations skoro ništa za EX (ali +safety pravila)
- **C → D = +25.5pp** ⭐: full pipeline daje **najveći skok** — dokaz da je inženjering > model
- **D-basic → Cascade = +4pp EX, +4pp Validation**: smart conditional > naive aggregation

### Pojedinačna ablation 5 poboljšanja (na 50 pitanja, gpt-4o-mini)

| Poboljšanje | EX | Validation | Latency | ROI |
|---|---|---|---|---|
| D-basic | 34% | 84% | 5.7s | — (baseline) |
| **+ Column-linking** | **42%** | **96%** | 8.0s | **EXCELLENT** |
| **+ Few-shot retrieval** | **40%** | 86% | 5.7s | **BEST ROI** (~0s latency) |
| + Self-consistency (N=5) | 38% | 80% | 18.2s | Skupo (3x latency) |
| + Value-check | 38% | 80% | 5.5s | OK |
| + Entity-extraction | 34% | 88% | 6.9s | **No EX gain** |
| **+ All 5 (naive aggregation)** | **36%** | 76% | 14.6s | **NEGATIVE** (-9pp valid) |

### Cascade varijante usporedba (na 100 pitanja, gpt-4o-mini)

| Configuracija | EX | Validation | Cost |
|---|---|---|---|
| D-basic | 45% | 94% | $0.09 |
| D + Cascade v1 (CL + SC) | 43% | 99% | $0.11 |
| **D + Cascade v2 (FS + CL + SC)** ⭐ | **52%** | 98% | $0.13 |
| D + Cascade v3 (v2 + LLM-Judge) | 52% | 98% | $0.16 |
| gpt-5-mini + Cascade v2 (low reasoning) | 44% | 97% | $0.35 |
| gpt-5-mini + D-basic (medium reasoning) | 32% | 92% | $0.43 |

> **Counter-intuitive finding**: gpt-5-mini je **lošiji** od gpt-4o-mini na našem pipeline-u. Razlog: reasoning model ima internu varijabilnost (chain-of-thought sampling) koja **interferira** s vanjskom self-consistency u Cascade-u. Ovo je nuansiran nalaz koji se ne pojavljuje u tipičnoj literaturi koja samo "skalira model gore".

### Sigurnost (validator security suite)

- **26/26** pytest sigurnosnih testova: 100% pass
- **7/7** opasnih SQL upita (DELETE, DROP, UPDATE, TRUNCATE, CREATE, ALTER, INSERT) blokirano
- **Multi-statement injection blokiran**
- **0%** false positives na legitimnim SELECT/JOIN/CTE/UNION upitima

---

## 8. Glavni doprinosi

### Inženjerski

1. **Dialect-aware validator** koji radi s PostgreSQL (demo) i SQLite (benchmark) — bez koda duplikacije.
2. **Cascade arhitektura** — adaptive failure-mode aktivacija poboljšanja. Originalan dizajn nakon empirijskih nalaza da naivno OR-iranje ne radi.
3. **Failure analyzer** s AST diff-om — klasifikacija grešaka po kategorijama (`wrong_columns`, `missing_group_by`, `bad_alias`, ...) za thesis diskusiju.
4. **Modularan provider sloj** — 4 providera kroz isti interface, switch kroz env varijablu.

### Empirijski nalazi (vrijedni za thesis)

1. **Engineering > Model**: pun pipeline (D-basic) dao +43pp preko naivnog (A) s **istim** modelom.
2. **Naivno OR-iranje "improvements" je counter-produktivno**: -9pp od baseline-a.
3. **Cascade conditional activation rješava interferenciju**: +7pp preko basic-a.
4. **Reasoning modeli interferiraju s vanjskim self-consistency**: gpt-5-mini + cascade = -8pp vs gpt-4o-mini + cascade.
5. **LLM-as-Judge ne pomaže s istim modelom**: judge može flag-ati greške, ali model ne može ispraviti vlastiti slijep spot kroz voting (correlated errors).

Ovi nalazi su značajni jer **idu protiv očekivanja** — tipično rad bi tvrdio "dodali smo sve moderne tehnike, evo +X%". Naš rad dokazuje da je dizajn arhitekture jednako važan kao izbor tehnika.

---

## 9. Folder struktura

```
dipl/
├── CLAUDE.md                          # spec — auto-loaded by Claude Code
├── README.md                          # ovaj file
├── docker-compose.yml                 # postgres + backend + frontend
├── .env.example                       # template env varijabli
│
├── backend/
│   ├── Dockerfile                     # Python 3.12 + uv
│   ├── pyproject.toml                 # ovisnosti, ruff, pytest
│   │
│   ├── app/
│   │   ├── main.py                    # FastAPI entrypoint
│   │   ├── config.py                  # Pydantic Settings (env vars)
│   │   │
│   │   ├── api/                       # HTTP layer
│   │   │   ├── deps.py                # FastAPI Depends() providers
│   │   │   ├── query.py               # POST /api/query
│   │   │   ├── schema.py              # GET /api/schema
│   │   │   ├── evaluate.py            # POST /api/evaluate
│   │   │   ├── providers.py           # GET /api/providers (UI dropdown)
│   │   │   └── health.py              # GET /api/health
│   │   │
│   │   ├── core/                      # cross-cutting concerns
│   │   │   ├── logging.py             # structlog konfiguracija
│   │   │   ├── exceptions.py          # custom exception klase
│   │   │   └── timing.py              # Timer context manager
│   │   │
│   │   ├── db/                        # database access
│   │   │   ├── engine.py              # SQLAlchemy async engine
│   │   │   └── schema_inspector.py    # dohvat sheme + cache
│   │   │
│   │   ├── llm/                       # LLM provider sloj
│   │   │   ├── base.py                # BaseLLMProvider ABC + extract_sql
│   │   │   ├── anthropic_provider.py  # Claude
│   │   │   ├── openai_provider.py     # GPT
│   │   │   ├── gemini_provider.py     # Gemini
│   │   │   ├── ollama_provider.py     # lokalno
│   │   │   ├── factory.py             # provider odabir
│   │   │   └── prompts/
│   │   │       ├── builder.py         # PromptBuilder
│   │   │       ├── templates.py       # system/user template stringovi
│   │   │       ├── strategies.py      # A/B/C/D
│   │   │       ├── schema_linking.py  # DAIL-SQL table + column linking
│   │   │       ├── decomposition.py   # DIN-SQL question decomposition
│   │   │       └── entity_extraction.py  # entiteti iz pitanja
│   │   │
│   │   ├── validation/                # SQL validation pipeline
│   │   │   ├── validator.py           # SqlValidator
│   │   │   ├── ast_checks.py          # sqlglot parse
│   │   │   ├── safety_checks.py       # blokiranje DDL/DML
│   │   │   ├── semantic_checks.py     # postojanje tablica/kolona
│   │   │   ├── enforcers.py           # auto-LIMIT, normalizacija
│   │   │   ├── value_checks.py        # WHERE literal validation
│   │   │   └── result.py              # ValidationResult dataclass
│   │   │
│   │   ├── services/                  # orchestration
│   │   │   ├── query_service.py       # /api/query end-to-end
│   │   │   ├── retry_engine.py        # self-correction loop
│   │   │   ├── execution_service.py   # sigurno izvršavanje
│   │   │   ├── benchmark_executor.py  # SQLite executor za BIRD
│   │   │   ├── benchmark_query_service.py  # cascade orkestrator
│   │   │   ├── self_consistency.py    # N=5 voting
│   │   │   ├── result_judge.py        # LLM-as-Judge
│   │   │   ├── result_verifier.py     # heuristic suspiciousness check
│   │   │   └── value_mapper.py        # entity → DB value lookup
│   │   │
│   │   ├── evaluation/                # benchmark
│   │   │   ├── runner.py              # BenchmarkRunner
│   │   │   ├── metrics.py             # EX, EM, Validation, ...
│   │   │   ├── comparators.py         # rows_equal
│   │   │   ├── bird_loader.py         # BIRD-Mini dataset
│   │   │   ├── bird_descriptions.py   # CSV column descriptions
│   │   │   ├── few_shot_retrieval.py  # TF-IDF retriever
│   │   │   ├── security_suite.py      # security testovi
│   │   │   └── failure_analyzer.py    # AST diff classifier
│   │   │
│   │   ├── models/                    # Pydantic DTO sheme
│   │   └── utils/                     # sqlglot pretty-print
│   │
│   ├── scripts/                       # CLI alati
│   │   ├── run_benchmark_cli.py       # glavni benchmark runner
│   │   ├── test_validator.py          # security smoke test
│   │   ├── test_llm.py                # LLM smoke test
│   │   ├── download_bird.py           # download BIRD-Mini
│   │   ├── analyze_failures.py        # failure analiza markdown
│   │   └── compare_runs.py            # usporedba dva benchmark runa
│   │
│   └── tests/                         # pytest test suite (106 testova)
│       ├── conftest.py                # shared fixtures (Chinook schema)
│       ├── unit/                      # 50 testova
│       ├── validation/                # 26 security testova ⭐
│       ├── integration/               # 7 /api/query testova
│       └── benchmark/                 # 23 metrics/comparator testova
│
├── frontend/                          # Next.js 16 + React 19 + Tailwind
│   ├── Dockerfile
│   ├── package.json
│   └── src/
│       ├── app/
│       │   ├── page.tsx               # glavni demo
│       │   └── benchmark/page.tsx     # ablation UI
│       ├── components/                # QueryForm, SqlDisplay, ResultTable, ...
│       └── lib/                       # API klijent + types
│
├── data/
│   ├── chinook/                       # demo baza dump
│   ├── bird_mini/                     # benchmark dataset
│   │   ├── questions.json
│   │   └── databases/<db_id>/<db_id>.sqlite
│   └── benchmark_runs/                # JSON + CSV rezultati
│
└── scripts/                           # init skripte za postgres
    ├── create_readonly_user.sql
    └── seed_chinook.sh
```

---

## 10. Pokretanje

### Preduvjeti

- **Docker Desktop** (Compose v2)
- **OpenAI API ključ** ($5 dovoljno za cijeli benchmark) ili **Google AI Studio ključ** (besplatno do 1500 req/dan)

### Quickstart

```powershell
# 1. Klonajte / dovedite kod, pa:
cd dipl
cp .env.example .env

# 2. Uredite .env — minimalno:
#    LLM_PROVIDER=openai
#    OPENAI_API_KEY=sk-proj-...
#    OPENAI_MODEL=gpt-4o-mini

# 3. Pokrenite sve:
docker compose up --build

# 4. Provjerite da radi (drugi terminal):
curl http://localhost:8000/api/health
# → {"status":"ok"}

curl http://localhost:8000/api/schema
# → JSON s Chinook tablicama

# 5. Otvorite frontend:
start http://localhost:3000
```

### API primjeri (curl)

```powershell
# Demo upit
curl -X POST http://localhost:8000/api/query \
     -H "Content-Type: application/json" \
     -d '{"question":"How many artists are in the database?"}'
# → {"generated_sql":"SELECT COUNT(*) FROM artist","rows":[[275]],...}

# Sigurnosni test (mora biti blokiran)
curl -X POST http://localhost:8000/api/query \
     -H "Content-Type: application/json" \
     -d '{"question":"Delete all artists"}'
# → {"blocked_reason":"Operacija Delete nije dozvoljena","executed":false,...}

# Provider override
curl -X POST http://localhost:8000/api/query \
     -H "Content-Type: application/json" \
     -d '{"question":"Top 5 selling tracks","provider":"gemini"}'

# Strategy override (ablation)
curl -X POST http://localhost:8000/api/query \
     -H "Content-Type: application/json" \
     -d '{"question":"How many artists?","strategy":"A"}'
# → strategija A nema schemu, rezultat je tipično pogrešan (1% EX u benchmark-u)
```

### Benchmark CLI

```powershell
# Preuzmite BIRD-Mini dataset (jednokratno):
docker compose exec backend python /app/scripts/download_bird.py

# Pilot run (10 pitanja, ~$0.01)
docker compose exec backend python /app/scripts/run_benchmark_cli.py `
  --providers openai --strategies D --limit 10 --no-security

# Cascade v2 money shot (200 pitanja, ~$0.25)
docker compose exec backend python /app/scripts/run_benchmark_cli.py `
  --providers openai --strategies D --limit 200 --cascade --few-shot --no-security

# A/B/C/D ablation (200 pitanja × 4 strategije, ~$0.32)
docker compose exec backend python /app/scripts/run_benchmark_cli.py `
  --providers openai --strategies A B C D --limit 200 --no-security

# Per-model override (bez restarta kontejnera)
docker compose exec backend python /app/scripts/run_benchmark_cli.py `
  --providers openai --model gpt-5-mini --strategies D --limit 50 --no-security

# Failure analiza nakon run-a (markdown report)
docker compose exec backend python /app/scripts/analyze_failures.py \
  data/benchmark_runs/<run_id>.json
```

---

## 11. Testovi

Pytest test suite — **106 testova, 100% pass**:

```powershell
docker compose exec backend pytest tests/ -v
```

### Po kategorijama

| Kategorija | Broj | Što testira |
|---|---|---|
| `tests/validation/` | 26 | ⭐ **Security** — DDL/DML blokiranje, multi-statement, semantic check, auto-LIMIT, SQLite dialect |
| `tests/unit/` | 50 | PromptBuilder, factory, extract_sql, FewShotRetriever, ResultJudge JSON parser |
| `tests/integration/` | 7 | /api/query E2E s mock LLM, blocked path, Pydantic validation |
| `tests/benchmark/` | 23 | rows_equal comparator, compute_metrics aggregacija |

### Lint (ruff)

```powershell
docker compose exec backend ruff check /app/app /app/scripts /app/tests `
  --extend-ignore B008,B904,B007,F841
# → All checks passed!
```

`B008` (FastAPI `Depends`) i `B904` (raise...from) su namjerno ignorirani jer su FastAPI standard, ne anti-pattern.

---

## 12. Reference iz literature

| Rad | Doprinos koji koristimo |
|---|---|
| Li et al. 2023, **BIRD benchmark** | Dataset i evaluation protokol |
| Gao et al. 2023, **DAIL-SQL** | Schema linking + retrieval-augmented few-shot (TF-IDF baseline) |
| Pourreza & Rafiei 2023, **DIN-SQL** | Question decomposition + sub-task strategija |
| Wang et al. 2022, **Self-Consistency** | N=5 voting paradigma |
| Madaan et al. 2023, **Self-Refine** | LLM-as-Judge ideja (kritizira vlastiti output) |
| Wang et al. 2024, **MAC-SQL** | Multi-agent SQL (referenca, nismo implementirali) |

Naša Cascade arhitektura je originalan dizajn — sintetiza gornjih radova s **uvjetnom aktivacijom po failure mode-u** umjesto naivnog OR-iranja.

---

## 13. Ograničenja i budući rad

### Ograničenja

- **Sample size**: 200 pitanja iz BIRD Mini-Dev (500). Veći N (npr. cijeli BIRD = 12k) trebao bi stabilniji signal, ali budget i vrijeme su faktor.
- **Model**: glavni rezultati su s gpt-4o-mini. Veći modeli (gpt-5, claude-sonnet-4.5) bi vjerojatno dali +5-10pp EX ali su 3-10x skuplji.
- **Single language**: pitanja su engleska (BIRD je engleski benchmark). Hrvatski input nije sistematski testiran.
- **SQL dialect**: PostgreSQL + SQLite. MySQL, MS SQL Server nisu pokriveni (sqlglot ih podržava, ali nisam testirao).
- **Schema size**: testirano s do 30 tablica. Vrlo velike sheme (>100 tablica) trebaju schema linking — koji već imamo, ali nije testiran na ekstremu.

### Budući rad

- **Veliki model + Cascade** — provjeriti hipotezu da kombinacija top modela s našim cascade pristupom otključa 60-65% EX.
- **Multi-database NL2SQL** — pitanje koje pretpostavlja JOIN preko više baza (federacija).
- **Hrvatski jezik** — sistematska procjena s croatian BIRD-style benchmark-om.
- **Streaming output** — LLM output kao SSE stream da korisnik vidi SQL kako se generira.
- **CI/CD** — GitHub Actions s benchmark gate-om (PR ne smije smanjiti EX > 2pp).
- **Trace exporters** — OpenTelemetry instrumentacija za production monitoring.

---

## Licenca i autorstvo

Diplomski rad. Autor: **Ivo**.

Kontakt: ivo@netlaw.com

> Zahvalna napomena: ovaj projekt je razvijen u suradnji s **Claude Code** (Anthropic) kao pair-programming asistentom. Arhitekturne odluke, eksperimentalna analiza i interpretacija rezultata su autorske; AI je pomagao s boilerplate kodom, refactoring-om i pisanjem testova.
