"""Runtime configuration loaded from environment variables."""

import os
from types import SimpleNamespace

APP_VERSION = "0.1.0"

# PostgreSQL connection — schema introspection + Chinook demo.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/chinook")

# Read-only PostgreSQL — primary safety layer at the DB level (GRANT SELECT only).
READONLY_DATABASE_URL = os.environ.get("READONLY_DATABASE_URL", "postgresql+asyncpg://nl2sql_readonly:readonly@localhost:5432/chinook")

# OpenAI credentials
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "")

# Optional — target an OpenAI-compatible endpoint (GitHub Models, OpenRouter, Groq, local vLLM). Empty = official api.openai.com.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")

# Reasoning effort hint for GPT-5 family / o-series only.
# Values: "minimal" | "low" | "medium" | "high" | "" (skip).
# Classic models (gpt-4o-mini) reject this parameter — leave empty.
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "")

# Runtime behaviour
QUERY_TIMEOUT_SECONDS = int(os.environ.get("QUERY_TIMEOUT_SECONDS", "10"))
DEFAULT_LIMIT = int(os.environ.get("DEFAULT_LIMIT", "1000"))
MAX_RETRY_ATTEMPTS = int(os.environ.get("MAX_RETRY_ATTEMPTS", "2"))
SCHEMA_CACHE_TTL_SECONDS = int(os.environ.get("SCHEMA_CACHE_TTL_SECONDS", "300"))

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = os.environ.get("LOG_FORMAT", "console")


# Namespace shim — keeps the `settings.X` access pattern working
settings = SimpleNamespace(
    DATABASE_URL=DATABASE_URL,
    READONLY_DATABASE_URL=READONLY_DATABASE_URL,
    OPENAI_API_KEY=OPENAI_API_KEY,
    OPENAI_MODEL=OPENAI_MODEL,
    OPENAI_BASE_URL=OPENAI_BASE_URL,
    OPENAI_REASONING_EFFORT=OPENAI_REASONING_EFFORT,
    QUERY_TIMEOUT_SECONDS=QUERY_TIMEOUT_SECONDS,
    DEFAULT_LIMIT=DEFAULT_LIMIT,
    MAX_RETRY_ATTEMPTS=MAX_RETRY_ATTEMPTS,
    SCHEMA_CACHE_TTL_SECONDS=SCHEMA_CACHE_TTL_SECONDS,
    LOG_LEVEL=LOG_LEVEL,
    LOG_FORMAT=LOG_FORMAT,
)
