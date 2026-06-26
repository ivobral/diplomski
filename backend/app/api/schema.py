"""GET /api/schema — fetch the database schema.

Supports two sources, switched via the optional ``database`` query param:

- ``database=chinook`` (default) — PostgreSQL Chinook demo, uses the main
  ``SchemaInspector`` configured against ``DATABASE_URL``.
- ``database=<bird_db_id>`` — one of the BIRD Mini-Dev SQLite databases.
  We reuse ``BenchmarkExecutor``'s engine cache (read-only SQLite URI)
  and run ``SchemaInspector`` against it.

The frontend uses ``/api/databases`` to learn which values are valid.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import (
    get_benchmark_executor,
    get_bird_loader,
    get_schema_inspector,
)
from app.db.schema_inspector import SchemaInspector
from app.evaluation.bird_loader import BirdLoader
from app.models.schema import ColumnDTO, ForeignKeyDTO, SchemaResponse, TableDTO
from app.services.benchmark_executor import BenchmarkExecutor

router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("", response_model=SchemaResponse)
async def get_schema(
    database: str = "chinook",
    refresh: bool = False,
    inspector: SchemaInspector = Depends(get_schema_inspector),
    bench_executor: BenchmarkExecutor = Depends(get_benchmark_executor),
    bird_loader: BirdLoader = Depends(get_bird_loader),
) -> SchemaResponse:
    """Return the schema of the requested database.

    Args:
        database: ``"chinook"`` or a BIRD database id. Default Chinook.
        refresh: ignore cache and re-introspect (only relevant for Chinook
            inspector — BIRD schemas are introspected fresh anyway).
    """

    if database == "chinook":
        schema = await inspector.get_schema(force_refresh=refresh)
    else:
        # Validate that BIRD is available and the requested db exists.
        if not bird_loader.is_ready():
            raise HTTPException(
                status_code=404,
                detail=(
                    "BIRD dataset is not available. Run "
                    "`docker compose exec backend python /app/scripts/download_bird.py`."
                ),
            )
        if database not in bird_loader.list_databases():
            raise HTTPException(
                status_code=404,
                detail=f"Unknown BIRD database: '{database}'",
            )
        # noqa: SLF001 — intentional reuse of the cached read-only engine.
        bird_engine = bench_executor._get_engine(database)
        bird_inspector = SchemaInspector(engine=bird_engine)
        schema = await bird_inspector.get_schema(force_refresh=True)

    # Convert internal dataclasses to the public DTO.
    tables = [
        TableDTO(
            name=t.name,
            columns=[
                ColumnDTO(
                    name=c.name,
                    data_type=c.data_type,
                    nullable=c.nullable,
                    is_primary_key=c.is_primary_key,
                )
                for c in t.columns
            ],
            foreign_keys=[
                ForeignKeyDTO(
                    constrained_columns=list(fk.constrained_columns),
                    referred_table=fk.referred_table,
                    referred_columns=list(fk.referred_columns),
                )
                for fk in t.foreign_keys
            ],
        )
        for t in schema.tables
    ]

    return SchemaResponse(tables=tables, fetched_at=schema.fetched_at)
