"""API DTO-i za /api/schema endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnDTO(BaseModel):
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool


class ForeignKeyDTO(BaseModel):
    constrained_columns: list[str]
    referred_table: str
    referred_columns: list[str]


class TableDTO(BaseModel):
    name: str
    columns: list[ColumnDTO]
    foreign_keys: list[ForeignKeyDTO]


class SchemaResponse(BaseModel):
    """Vraća se na GET /api/schema — kompletna shema baze."""

    tables: list[TableDTO]
    fetched_at: float = Field(description="Unix timestamp dohvata sheme (za debugging cache-a).")
