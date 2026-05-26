"""Ollama lokalni LLM provider.

Ollama nudi REST API na `http://<host>:11434`. Koristimo izravni
``httpx.AsyncClient`` — niti Ollama SDK ni LangChain wrapper nisu potrebni.

Konfiguracija:
- ``OLLAMA_BASE_URL`` (default ``http://host.docker.internal:11434`` jer
  backend radi u Dockeru a Ollama na hostu)
- ``OLLAMA_MODEL`` — npr. ``sqlcoder``, ``llama3``. Mora biti `ollama pull`-an.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.core.exceptions import ConfigurationError, LLMError
from app.core.logging import get_logger
from app.core.timing import Timer
from app.llm.base import BaseLLMProvider, LLMResponse, Prompt, extract_sql

logger = get_logger(__name__)


class OllamaProvider(BaseLLMProvider):
    """Provider za lokalne modele kroz Ollama REST API."""

    def __init__(self, model: str | None = None) -> None:
        """Konstruktor s opcionalnim model override-om.

        Args:
            model: Ako je dato, koristi se umjesto ``settings.OLLAMA_MODEL``.
                Korisno iz CLI runnera (npr. `--model qwen2.5-coder:7b`).
                None → fallback na env varijablu.
        """

        effective_model = model or settings.OLLAMA_MODEL
        if not effective_model:
            raise ConfigurationError(
                "OLLAMA_MODEL nije postavljen (ni u .env ni preko model parametra). "
                "Prvo `ollama pull <name>` na hostu, zatim upiši ime."
            )
        self._base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self._model = effective_model
        # Timeout je velikodušan jer Ollama na CPU-u zna trajati 60-180s po
        # pitanju (model load + generation). Anthropic/OpenAI tipično <3s.
        # 5 min daje prostora i za hladni startup modela (prvi upit nakon idle).
        self._timeout = httpx.Timeout(300.0, connect=10.0)

    def name(self) -> str:
        return "ollama"

    async def generate(self, prompt: Prompt) -> LLMResponse:
        # /api/chat endpoint očekuje listu poruka u OpenAI-stilu. Stream=false
        # da dobijemo cijeli odgovor u jednom JSON-u, ne SSE stream.
        payload = {
            "model": self._model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }

        with Timer() as t:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        f"{self._base_url}/api/chat",
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
            except httpx.HTTPError as exc:
                logger.exception("ollama.http.error")
                raise LLMError(
                    f"Ollama API greška: {exc}. "
                    f"Provjeri da Ollama radi na {self._base_url} "
                    f"i da je `{self._model}` pull-an."
                ) from exc

        raw_text = data.get("message", {}).get("content", "")
        sql = extract_sql(raw_text)

        return LLMResponse(
            sql=sql,
            raw_text=raw_text,
            model=self._model,
            latency_ms=t.elapsed_ms,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )
