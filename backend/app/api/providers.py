"""GET /api/providers — currently active LLM provider info.

Returns a single-element list (always OpenAI in this build) — the UI uses
it to show which model is in use. The response shape is kept generic so a
multi-provider variant could be added later without changing clients.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.llm.factory import list_configured_providers

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderInfo(BaseModel):
    """One provider — what the UI renders as the active-model badge."""

    name: str
    model: str
    base_url: str | None = None


class ProvidersResponse(BaseModel):
    default: str
    available: list[ProviderInfo]


@router.get("", response_model=ProvidersResponse)
async def get_providers() -> ProvidersResponse:
    """Return the currently configured provider."""

    available = [ProviderInfo(**entry) for entry in list_configured_providers()]
    return ProvidersResponse(default="openai", available=available)
