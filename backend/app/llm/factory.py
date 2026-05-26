"""Factory za odabir aktivnog LLM providera.

Bira konkretnu implementaciju (Anthropic / OpenAI / Ollama) prema
``settings.LLM_PROVIDER``. Razlog za factory pattern: postoji točno 3
konkretne varijacije koje se mijenjaju kroz config, i sve dijele
``BaseLLMProvider`` interface — to je idealno mjesto za Factory.

Korištenje (preko DI u FastAPI, vidi app/api/deps.py):

    provider = get_llm_provider()
    response = await provider.generate(prompt)
"""

from __future__ import annotations

from app.config import settings
from app.core.exceptions import ConfigurationError
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import BaseLLMProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.ollama_provider import OllamaProvider
from app.llm.openai_provider import OpenAIProvider


def create_llm_provider() -> BaseLLMProvider:
    """Vraća instancu providera prema ``settings.LLM_PROVIDER``.

    Pristupanje konfiguraciji odabranog providera (API ključ, model) radi
    se u konstruktoru konkretne klase — ako je nešto nepopunjeno, baca
    ``ConfigurationError`` već ovdje, ne kasnije pri prvom upitu.
    """

    return create_llm_provider_for(settings.LLM_PROVIDER)


def create_llm_provider_for(
    name: str,
    model: str | None = None,
) -> BaseLLMProvider:
    """Vraća instancu providera za eksplicitno zadano ime.

    Koristi se za per-request override (frontend dropdown) i per-run override
    iz CLI-a (benchmark runner). Razdvojeno od ``create_llm_provider`` jer
    ne čita ``settings.LLM_PROVIDER``.

    Args:
        name: ime providera (anthropic | openai | ollama | gemini).
        model: opcionalan override imena modela. Ako None, koristi se
            odgovarajuća env varijabla (npr. ``OPENAI_MODEL``).
            Korisno za CLI benchmark runove gdje želimo isti pipeline
            testirati s različitim modelima bez restarta backend kontejnera.

    Raises:
        ConfigurationError: ako provider nije podržan ili konfiguracija
            nije popunjena (API ključ ili model).
    """

    match name:
        case "anthropic":
            return AnthropicProvider(model=model)
        case "openai":
            return OpenAIProvider(model=model)
        case "ollama":
            return OllamaProvider(model=model)
        case "gemini":
            return GeminiProvider(model=model)
        case _:
            raise ConfigurationError(
                f"Nepoznat LLM provider: {name!r}. "
                f"Dozvoljeno: anthropic | openai | ollama | gemini."
            )


def list_configured_providers() -> list[dict[str, str]]:
    """Vrati listu providera čija je konfiguracija popunjena u ``settings``.

    Provjera je čisto introspekcijska (provjera env varijabli) — ne radi
    auth ping prema API-ju. Brzo i bez troškova. Koristi se za UI
    dropdown da prikaže samo ono što stvarno može raditi.
    """

    available: list[dict[str, str]] = []

    if settings.ANTHROPIC_API_KEY and settings.ANTHROPIC_MODEL:
        available.append({
            "name": "anthropic",
            "model": settings.ANTHROPIC_MODEL,
        })

    if settings.OPENAI_API_KEY and settings.OPENAI_MODEL:
        entry = {
            "name": "openai",
            "model": settings.OPENAI_MODEL,
        }
        # base_url je opcionalan — ako je postavljen, ovaj "openai" zapravo
        # ide na GitHub Models / OpenRouter / Groq / itd. UI to može prikazati.
        if settings.OPENAI_BASE_URL:
            entry["base_url"] = settings.OPENAI_BASE_URL
        available.append(entry)

    if settings.OLLAMA_MODEL:
        available.append({
            "name": "ollama",
            "model": settings.OLLAMA_MODEL,
        })

    if settings.GEMINI_API_KEY and settings.GEMINI_MODEL:
        available.append({
            "name": "gemini",
            "model": settings.GEMINI_MODEL,
        })

    return available
