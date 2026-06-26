"""API DTO schemas for /api/query — request input and response output.

- ``QueryRequest``       — incoming question + optional strategy / database
- ``QueryResponse``      — generated SQL, execution result, status, latency
- ``LatencyBreakdown``   — per-phase timing (LLM / validation / execution)
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """User question in natural language."""

    question: str = Field(min_length=1, max_length=2000)
    # Strategy controls how much context goes to the LLM. None → D (full
    # pipeline). Other values (A/B/C) exist only for the ablation experiment.
    strategy: Literal["A", "B", "C", "D"] | None = None
    # Database id — ``"chinook"`` (Postgres demo) or a BIRD SQLite db_id.
    # The handler dispatches to the right pipeline based on this.
    database: str = "chinook"
    # Reserved — kept so the UI can pre-emptively pass a provider override.
    # Single-provider build: only ``"openai"`` is accepted by the factory.
    provider: Literal["openai"] | None = None


class LatencyBreakdown(BaseModel):
    """Per-phase latency in milliseconds — granular for honest benchmarks."""

    prompt_build_ms: float | None = None    # schema fetch + template formatting
    llm_ms: float | None = None             # LLM API call(s), cumulative over retries
    validation_ms: float | None = None      # AST + safety + semantic + enforcers
    execution_ms: float | None = None       # read-only DB query
    total_ms: float | None = None


class QueryResponse(BaseModel):
    """Result of /api/query — exactly what the frontend renders."""

    question: str
    generated_sql: str | None = None
    normalized_sql: str | None = None
    validated: bool = False
    executed: bool = False
    error: str | None = None
    blocked_reason: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    latency: LatencyBreakdown = Field(default_factory=LatencyBreakdown)
    retry_count: int = 0
