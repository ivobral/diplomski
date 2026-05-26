"""Konfiguracija aplikacije — sva podešavanja se čitaju iz env varijabli.

Ovaj modul je jedino mjesto gdje aplikacija dohvaća konfiguraciju. Razlog:
- bez magic stringova razasutih po kodu,
- type-safe pristup preko Pydantic Settings,
- centralizirano mjesto za default vrijednosti i validaciju.

Korištenje:

    from app.config import settings
    print(settings.LLM_PROVIDER)

Vrijednosti se popunjavaju iz `.env` file-a u root-u projekta (vidi
`.env.example` za sve podržane ključeve).
"""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Sva runtime konfiguracija backend servisa.

    Klasa je Pydantic Settings — ime atributa direktno mapira na ime env
    varijable. Tipovi su validirani pri pokretanju aplikacije, pa neispravan
    konfig odmah pada s razumljivom porukom umjesto da fail-a kasnije.
    """

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/chinook",
        description="Glavni DB connection string (čita schemu, migracije).",
    )
    READONLY_DATABASE_URL: str = Field(
        default="postgresql+asyncpg://nl2sql_readonly:readonly@localhost:5432/chinook",
        description=(
            "Read-only konekcija — koristi se isključivo za izvršavanje "
            "generiranih SQL upita. Glavni sigurnosni sloj na razini baze."
        ),
    )

    # ------------------------------------------------------------------
    # LLM provider
    # ------------------------------------------------------------------
    # Literal tip ograničava dozvoljene vrijednosti — pogrešna konfiguracija
    # pada pri startup-u, ne pri prvom pozivu LLM-a.
    LLM_PROVIDER: Literal["anthropic", "openai", "ollama", "gemini"] = "anthropic"

    # Imena modela NIKAD nisu hardkodirana — ova polja imaju prazan default
    # i moraju biti popunjena u .env za providera koji se aktivira preko
    # LLM_PROVIDER. Razlog: konkretni model nazivi se mijenjaju brzo
    # (deprecation, novi modeli), ovise o pravima korisničkog API računa,
    # i ne smiju biti "skrivene odluke" u kodu.
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = ""

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = ""
    # OPENAI_BASE_URL omogućuje korištenje OpenAI-kompatibilnih API-ja
    # (GitHub Models, OpenRouter, Groq, lokalni vllm/llama.cpp servisi).
    # Prazno = koristi službeni OpenAI endpoint (api.openai.com).
    # Primjeri:
    #   GitHub Models:  https://models.github.ai/inference
    #   OpenRouter:     https://openrouter.ai/api/v1
    #   Groq:           https://api.groq.com/openai/v1
    OPENAI_BASE_URL: str = ""
    # OPENAI_REASONING_EFFORT — samo za reasoning modele (GPT-5 familija, o-serija).
    # Vrijednosti: "minimal" | "low" | "medium" | "high" | "" (default ponašanje).
    # Niža vrijednost = manje thinking tokena = brže + jeftinije, ali manja
    # kvaliteta na složenim pitanjima. Za SQL benchmark, "low" daje dobar
    # balance. Šalje se samo ako je non-empty (klasični modeli npr. gpt-4o-mini
    # ne podržavaju ovaj parametar i odbili bi request).
    OPENAI_REASONING_EFFORT: str = ""

    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = ""

    # --- Google Gemini ---
    # Generative Language API; AI Studio: https://aistudio.google.com/apikey
    # Free tier je velikodušan (15 RPM, 1500/dan) i bez kartice.
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = ""

    # ------------------------------------------------------------------
    # Ponašanje sustava
    # ------------------------------------------------------------------
    QUERY_TIMEOUT_SECONDS: int = 10
    DEFAULT_LIMIT: int = 1000
    MAX_RETRY_ATTEMPTS: int = 2
    SCHEMA_CACHE_TTL_SECONDS: int = 300

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"

    # ------------------------------------------------------------------
    # Pydantic Settings konfiguracija
    # ------------------------------------------------------------------
    # `extra="ignore"` — ako .env ima dodatne (npr. frontend) ključeve, ne
    # rušiti backend startup. Stricter mode bi bio "forbid".
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


# Globalni singleton — instancira se jednom pri import-u modula. Sva čitanja
# konfiguracije idu kroz ovaj objekt. Ako trebamo override (npr. u testovima),
# koristimo dependency override u FastAPI ili monkeypatch.
settings = Settings()
