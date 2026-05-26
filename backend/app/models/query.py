"""API DTO-i za /api/query endpoint.

Faza 1: definicije postoje, ali endpoint vraća 501. Faza 2 implementira
stvarno generiranje i izvršavanje.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """Korisničko pitanje na prirodnom jeziku."""

    question: str = Field(min_length=1, max_length=2000)
    # Strategija određuje koliko se konteksta šalje LLM-u. Polje je
    # opcionalno — ako nije zadano, koristi se najbogatija strategija
    # (D, s validacijom i retry-em). Korisno za eksperimente A/B/C/D.
    strategy: Literal["A", "B", "C", "D"] | None = None
    # Opcionalan override LLM providera — ako je naveden, koristi se za
    # ovaj request umjesto default-a iz settings.LLM_PROVIDER. Omogućava
    # frontend dropdown za usporedbu modela bez restart-a backenda.
    # ``None`` znači "koristi default providera" (Pydantic default).
    provider: Literal["anthropic", "openai", "ollama", "gemini"] | None = None


class LatencyBreakdown(BaseModel):
    """Mjerenje latencije po fazama — direktno za benchmark u Fazi 4."""

    llm_ms: float | None = None
    validation_ms: float | None = None
    execution_ms: float | None = None
    total_ms: float | None = None


class QueryResponse(BaseModel):
    """Rezultat /api/query — što frontend prikazuje."""

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
