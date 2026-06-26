"""GET /api/databases — list of databases available for querying.

Always includes ``chinook`` (the PostgreSQL demo). Additionally exposes
every BIRD Mini-Dev SQLite database that has been downloaded locally
(``data/bird_mini/databases/<db_id>/<db_id>.sqlite``). Used by the
frontend to populate the database picker.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_bird_loader
from app.evaluation.bird_loader import BirdLoader

router = APIRouter(prefix="/databases", tags=["databases"])


class DatabaseInfo(BaseModel):
    """Single database entry shown to the user."""

    id: str
    label: str
    dialect: str  # "postgres" | "sqlite"
    source: str   # "demo" | "bird"


class DatabasesResponse(BaseModel):
    default: str
    databases: list[DatabaseInfo]


@router.get("", response_model=DatabasesResponse)
def list_databases(
    bird_loader: BirdLoader = Depends(get_bird_loader),
) -> DatabasesResponse:
    """Return all databases the user can query."""

    items: list[DatabaseInfo] = [
        DatabaseInfo(
            id="chinook",
            label="Chinook (demo)",
            dialect="postgres",
            source="demo",
        ),
    ]
    # Add BIRD databases if the dataset has been downloaded.
    if bird_loader.is_ready():
        for db_id in bird_loader.list_databases():
            items.append(
                DatabaseInfo(
                    id=db_id,
                    label=_pretty_label(db_id),
                    dialect="sqlite",
                    source="bird",
                )
            )

    return DatabasesResponse(default="chinook", databases=items)


def _pretty_label(db_id: str) -> str:
    """Turn ``california_schools`` → ``California schools``."""

    return db_id.replace("_", " ").capitalize()
