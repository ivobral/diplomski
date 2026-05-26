"""GET /api/schema — dohvat sheme baze.

Ovaj endpoint je jedini "pravi" endpoint u Fazi 1 — pokazuje da
SchemaInspector radi end-to-end. U Fazi 2 isti SchemaInspector koristi
PromptBuilder za konstrukciju prompta.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_schema_inspector
from app.db.schema_inspector import SchemaInspector
from app.models.schema import ColumnDTO, ForeignKeyDTO, SchemaResponse, TableDTO

router = APIRouter(prefix="/schema", tags=["schema"])


@router.get("", response_model=SchemaResponse)
async def get_schema(
    inspector: SchemaInspector = Depends(get_schema_inspector),
    refresh: bool = False,
) -> SchemaResponse:
    """Vraća kompletnu shemu spojene baze.

    Args:
        refresh: Ako je ``True``, ignorira cache i radi novi introspection.

    Returns:
        ``SchemaResponse`` s listom tablica, kolona i foreign keyeva.
    """

    schema = await inspector.get_schema(force_refresh=refresh)

    # Pretvorba internih dataclass-ova u Pydantic DTO-e. Razdvojeno
    # namjerno (vidi docstring app/models/__init__.py) — interni model
    # može se mijenjati neovisno o API ugovoru.
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
