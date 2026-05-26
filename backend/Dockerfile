# =============================================================================
# Backend Dockerfile — Python 3.12 + uv
# =============================================================================
# Cilj: jednostavan, čitljiv image koji digne FastAPI servis. Bez multi-stage
# produkcijske optimizacije (size-shaving, distroless, itd.) — diplomski rad
# se boduje na evaluaciji i arhitekturi, ne na imageskupini.

FROM python:3.12-slim

# uv je moderni Python package manager — brži i deterministički od pip-a.
# Instaliramo ga iz službene slike (single-binary copy).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Sustavske ovisnosti — `libpq-dev` nije nužan za asyncpg, ali ostavljamo
# `build-essential` za eventualne native ekstenzije (sqlglot je čisti Python).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Kopiramo manifest prvo da iskoristimo Docker layer cache — ovisnosti se
# rebuilda samo kad se manifest promijeni, ne na svakom code change-u.
COPY pyproject.toml ./
RUN uv venv /opt/venv && \
    uv pip install --python /opt/venv/bin/python --no-cache -r pyproject.toml && \
    # Dev alati za testove i lint — instalirani u istom image-u jer projekt
    # je razvojnog karaktera (diplomski). Za produkciju bi se odvojili kroz
    # multi-stage build, ali tu je premise: jedan image = sve što treba.
    uv pip install --python /opt/venv/bin/python --no-cache \
        pytest pytest-asyncio ruff httpx

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Kopija aplikacijskog koda dolazi nakon ovisnosti — promjena koda neće
# invalidirati layer s ovisnostima.
COPY app ./app

EXPOSE 8000

# --reload uključen za razvojni tijek (compose mounta app/ kao read-only volume).
# Za produkciju bi se izbacio, ali u kontekstu diplomskog rada — razvojno radi.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
