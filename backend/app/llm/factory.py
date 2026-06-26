"""LLM provider factory"""

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from app.core.exceptions import ConfigurationError
from app.llm.base import BaseLLMProvider
from app.llm.openai_provider import OpenAIProvider


def create_llm_provider() -> BaseLLMProvider:
    return OpenAIProvider()


def create_llm_provider_for(name: str, model: str | None = None) -> BaseLLMProvider:
    if name != "openai":
        raise ConfigurationError(f"Unknown LLM provider: {name!r}. Only 'openai' is supported.")
    return OpenAIProvider(model=model)


def list_configured_providers() -> list[dict[str, str]]:
    if not (OPENAI_API_KEY and OPENAI_MODEL):
        return []
    entry: dict[str, str] = {"name": "openai", "model": OPENAI_MODEL}
    if OPENAI_BASE_URL:
        entry["base_url"] = OPENAI_BASE_URL
    return [entry]
