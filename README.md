# NL2SQL

Diplomski rad — sustav koji pretvara prirodno-jezična pitanja u SQL upite koristeći velike jezične modele, sa sigurnosnom validacijom i benchmark evaluacijom kroz BIRD Mini-Dev.

## Što sustav radi

Korisnik kroz web sučelje postavi pitanje na engleskom (npr. *"How many artists are in the database?"*). Sustav:

1. Dohvaća shemu odabrane baze (Chinook ili jedna od 11 BIRD baza).
2. Konstruira prompt s pitanjem, shemom i relacijama među tablicama.
3. Šalje LLM-u (OpenAI GPT-4o-mini) da generira SQL.
4. **Validira SQL** kroz strukturalnu (AST) analizu — blokira sve osim čistog SELECT-a.
5. Izvršava SQL kroz read-only DB konekciju.
6. Vraća rezultat korisniku s mjerenjem latencije po fazama.

## Sigurnost

Pet obrambenih slojeva koji rade neovisno:

- Uputa modelu da generira samo SELECT
- Strukturalna AST analiza koja blokira DDL/DML/injection (sqlglot)
- Read-only DB korisnik s `GRANT SELECT` only
- 60-sekundni timeout
- Automatski `LIMIT 1000` na neograničene upite

Validator je dokazano blokira 100% opasnih upita kroz 26 dediciranih pytest testova.

## Pokretanje

```bash
cp .env  # popuni OPENAI_API_KEY
docker compose up --build
```

Frontend: `http://localhost:3000` · Backend Swagger: `http://localhost:8000/docs`

## Arhitektura

```
Next.js UI ──HTTP──> FastAPI backend
                       │
                       ├─ SchemaInspector (dinamički dohvat)
                       ├─ PromptBuilder (4 strategije A/B/C/D)
                       ├─ LLMProvider (OpenAI)
                       ├─ SqlValidator (AST + safety + semantic)
                       └─ QueryExecutor (read-only)
                              │
                              ▼
                       PostgreSQL (Chinook) / SQLite (BIRD)
```

## Eksperimentalne strategije

4 razine konteksta koji se daje LLM-u:

- **A** — samo pitanje
- **B** — pitanje + popis tablica i kolona
- **C** — pitanje + tablice + veze (foreign keys)
- **D** — sve gore + schema linking + decomposition + sample rows + Chain-of-Thought

Plus **Cascade arhitektura** — uvjetno aktivira dodatne tehnike (column-linking, self-consistency) samo kad standardni pristup ne uspije.

## Rezultati (BIRD Mini-Dev, 200Q, GPT-4o-mini)

| Strategija | Execution Accuracy | Validation |
|---|---|---|
| A | 0% | 10% |
| B | 16.5% | 91% |
| C | 17.5% | 92% |
| D | 43% | 94% |
| **D + Cascade** | **47%** | **98.5%** |

## Tehnologije

Python 3.12 + FastAPI · SQLAlchemy 2.x async · sqlglot · OpenAI SDK · Next.js 16 + React 19 + TailwindCSS · PostgreSQL 16 · SQLite (aiosqlite) · Docker Compose

## Testovi

```bash
docker compose exec backend pytest tests/
```

101 test, 100% pass (26 dediciranih sigurnosnih).

## Reference iz literature

- Li et al. 2023 — **BIRD benchmark**
- Gao et al. 2023 — **DAIL-SQL** (schema linking + retrieval few-shot)
- Pourreza & Rafiei 2023 — **DIN-SQL** (question decomposition)
- Wang et al. 2022 — **Self-Consistency**
- Wei et al. 2022 — **Chain-of-Thought prompting**

## Autor

Ivo Bralić · ivo@netlaw.com
