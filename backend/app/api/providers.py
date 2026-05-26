"""GET /api/providers — popis konfiguriranih LLM providera.

Frontend zove ovaj endpoint pri startup-u da popuni dropdown providerom.
Vraćamo samo one čija je konfiguracija popunjena u ``.env`` (API_KEY +
MODEL za remote providere, MODEL za Ollama). Bez ovoga, UI bi morao
"pogađati" što je dostupno, ili korisnik ručno tipkati provider name.

Endpoint je čisto introspekcijski — ne radi auth ping prema providerima
(brzo, bez troška). Stvarni auth se događa pri prvom upitu.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.llm.factory import list_configured_providers

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderInfo(BaseModel):
    """Jedan provider u popisu — što UI prikazuje u dropdown-u."""

    name: str
    model: str
    base_url: str | None = None


class ProvidersResponse(BaseModel):
    """Lista konfiguriranih providera + ime default-a."""

    default: str
    available: list[ProviderInfo]


@router.get("", response_model=ProvidersResponse)
async def get_providers() -> ProvidersResponse:
    """Vrati listu providera s validnom konfiguracijom u ``.env``."""

    configured = list_configured_providers()
    available = [ProviderInfo(**entry) for entry in configured]

    return ProvidersResponse(
        default=settings.LLM_PROVIDER,
        available=available,
    )
