"""Anthropic Claude LLM provider.

Koristi službeni ``anthropic`` Python SDK (async varijantu). Bez wrapper
biblioteka — izravni poziv ``client.messages.create()``.

Konfiguracija (iz `app.config.settings`):
- ``ANTHROPIC_API_KEY`` — API ključ s console.anthropic.com
- ``ANTHROPIC_MODEL``   — ime modela (mora doći iz env, ne hardkodira se)

Ako bilo koji od ta dva nedostaje pri pokretanju providera, baca
``ConfigurationError`` — bolje pasti na startup-u nego pri prvom upitu.

Defensive ponašanje pri error-ima:
- **429 Rate Limit**: automatski retry s exponential backoff (max 5 puta) —
  Anthropic tier 1 ima 50 RPM, malo vjerojatno da hitnemo, ali defensive.
- **max_tokens** ograničeno na 1024 — sprječava skupe halucinacije.
- **Timeout** 60s po pozivu — paid Claude tipično odgovori za <3s.
"""

from __future__ import annotations

import asyncio

import httpx
from anthropic import APIError as AnthropicAPIError
from anthropic import APITimeoutError, AsyncAnthropic, RateLimitError

from app.config import settings
from app.core.exceptions import ConfigurationError, LLMError
from app.core.logging import get_logger
from app.core.timing import Timer
from app.llm.base import BaseLLMProvider, LLMResponse, Prompt, extract_sql

logger = get_logger(__name__)

_MAX_RATE_LIMIT_RETRIES = 5
_REQUEST_TIMEOUT = 60.0


class AnthropicProvider(BaseLLMProvider):
    """Provider za Anthropic Claude familiju modela."""

    def __init__(self, model: str | None = None) -> None:
        """Konstruktor s opcionalnim model override-om.

        Args:
            model: Ako je dato, koristi se umjesto ``settings.ANTHROPIC_MODEL``.
                Korisno iz CLI runnera za per-run promjenu modela.
                None → fallback na env varijablu.
        """

        if not settings.ANTHROPIC_API_KEY:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY nije postavljen u .env. "
                "Pribavi ključ na https://console.anthropic.com/settings/keys."
            )
        effective_model = model or settings.ANTHROPIC_MODEL
        if not effective_model:
            raise ConfigurationError(
                "ANTHROPIC_MODEL nije postavljen (ni u .env ni preko model parametra). "
                "Provjeri dostupne modele u Anthropic Console i upiši točno ime."
            )
        self._client = AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=10.0),
            # SDK ima default retry = 2; isključujemo da imamo svoj predvidljiv
            # i loggable mehanizam.
            max_retries=0,
        )
        self._model = effective_model

    def name(self) -> str:
        return "anthropic"

    async def generate(self, prompt: Prompt) -> LLMResponse:
        with Timer() as t:
            response = await self._call_with_retry(prompt)

        # Claude vraća listu content block-ova; za naš use case (text response)
        # uzimamo prvi text blok. Ako budu tool-use ili druge varijante, ovo
        # treba prilagoditi — u Fazi 2 koristimo isključivo text.
        raw_text = response.content[0].text if response.content else ""
        sql = extract_sql(raw_text)

        return LLMResponse(
            sql=sql,
            raw_text=raw_text,
            model=self._model,
            latency_ms=t.elapsed_ms,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
        )

    async def _call_with_retry(self, prompt: Prompt):
        """Pošalji request s retry-em na 429 (rate limit) i timeout."""

        last_exc: Exception | None = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                # max_tokens=1024 dovoljno za najsloženije SELECT-ove
                # (tipično 100-400 tokena). temperature=0 za determinizam.
                return await self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    temperature=0,
                    system=prompt.system,
                    messages=[{"role": "user", "content": prompt.user}],
                )
            except RateLimitError as exc:
                last_exc = exc
                if attempt >= _MAX_RATE_LIMIT_RETRIES:
                    logger.exception("anthropic.rate_limit.exhausted", attempts=attempt + 1)
                    raise LLMError(
                        f"Anthropic rate limit i dalje aktivan nakon {_MAX_RATE_LIMIT_RETRIES} retry-a: {exc}"
                    ) from exc
                wait_s = _get_retry_after(exc) or (2 ** (attempt + 1))
                logger.warning(
                    "anthropic.rate_limit.retry",
                    attempt=attempt + 1,
                    max=_MAX_RATE_LIMIT_RETRIES,
                    wait_s=wait_s,
                )
                await asyncio.sleep(wait_s)
                continue
            except APITimeoutError as exc:
                last_exc = exc
                if attempt >= 2:
                    logger.exception("anthropic.timeout.exhausted")
                    raise LLMError(f"Anthropic timeout (3 retry-a): {exc}") from exc
                logger.warning("anthropic.timeout.retry", attempt=attempt + 1)
                await asyncio.sleep(2 ** attempt)
                continue
            except AnthropicAPIError as exc:
                # Auth, billing, model_not_found, … — neretry-abilno.
                logger.exception("anthropic.api.error")
                raise LLMError(f"Anthropic API greška: {exc}") from exc

        raise LLMError(f"Anthropic nepoznata greška: {last_exc}")


def _get_retry_after(exc: RateLimitError) -> float | None:
    """Vrati `retry-after` header ako je dat."""

    try:
        response = getattr(exc, "response", None)
        if response is None:
            return None
        header = response.headers.get("retry-after")
        if header is None:
            return None
        return float(header)
    except (ValueError, AttributeError):
        return None
