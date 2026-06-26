"""GET /api/health — provjera zdravlja servisa.

Ovaj endpoint je namjerno trivijalan: vraća 200 ako proces radi. Ne
provjerava DB ni LLM provider — to bi se moglo dodati kao ``/health/full``
ako bude potrebno (npr. za production probe). Za diplomski demo ovo je
dovoljno.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import APP_VERSION

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=APP_VERSION)
