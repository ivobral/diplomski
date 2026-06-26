from __future__ import annotations

import asyncio

import httpx
from openai import APITimeoutError, AsyncOpenAI, OpenAIError, RateLimitError

from app.config import settings
from app.core.exceptions import ConfigurationError, LLMError
from app.core.logging import get_logger
from app.core.timing import Timer
from app.llm.base import BaseLLMProvider, LLMResponse, Prompt, extract_sql

logger = get_logger(__name__)

_MAX_RATE_LIMIT_RETRIES = 5
_REQUEST_TIMEOUT = 60.0


class OpenAIProvider(BaseLLMProvider):

    def __init__(self, model: str | None = None) -> None:
        if not settings.OPENAI_API_KEY:
            raise ConfigurationError(
                "OPENAI_API_KEY nije postavljen u .env. "
                "Za OpenAI: https://platform.openai.com/api-keys. "
                "Za GitHub Models koristi svoj GitHub PAT (settings → developer settings → tokens)."
            )
        effective_model = model or settings.OPENAI_MODEL
        if not effective_model:
            raise ConfigurationError(
                "OPENAI_MODEL nije postavljen (ni u .env ni preko model parametra). "
                "Provjeri dostupne modele kod tvog providera i upiši točno ime."
            )

        custom_base_url = settings.OPENAI_BASE_URL.strip()
        effective_base_url = custom_base_url or "https://api.openai.com/v1"

        self._client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=effective_base_url,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=10.0),
            max_retries=0,
        )
        self._model = effective_model
        self._base_url_label = custom_base_url or "openai-default"
        self._reasoning_effort = settings.OPENAI_REASONING_EFFORT.strip() or None

    def name(self) -> str:
        return f"openai({self._base_url_label})"

    async def generate(self, prompt: Prompt) -> LLMResponse:
        with Timer() as t:
            response = await self._call_with_retry(prompt)

        choice = response.choices[0]
        raw_text = choice.message.content or ""
        sql = extract_sql(raw_text)

        return LLMResponse(
            sql=sql,
            raw_text=raw_text,
            model=self._model,
            latency_ms=t.elapsed_ms,
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
        )

    async def _call_with_retry(self, prompt: Prompt):
        last_exc: Exception | None = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                params: dict = {
                    "model": self._model,
                    "max_completion_tokens": 4096,
                    "messages": [
                        {"role": "system", "content": prompt.system},
                        {"role": "user", "content": prompt.user},
                    ],
                }
                if self._reasoning_effort:
                    params["reasoning_effort"] = self._reasoning_effort

                return await self._client.chat.completions.create(**params)
            except RateLimitError as exc:
                last_exc = exc
                if attempt >= _MAX_RATE_LIMIT_RETRIES:
                    logger.exception("openai.rate_limit.exhausted", attempts=attempt + 1)
                    raise LLMError(
                        f"OpenAI rate limit i dalje aktivan nakon {_MAX_RATE_LIMIT_RETRIES} retry-a: {exc}"
                    ) from exc
                wait_s = _get_retry_after(exc) or (2 ** (attempt + 1))
                logger.warning(
                    "openai.rate_limit.retry",
                    attempt=attempt + 1,
                    max=_MAX_RATE_LIMIT_RETRIES,
                    wait_s=wait_s,
                )
                await asyncio.sleep(wait_s)
                continue
            except APITimeoutError as exc:
                last_exc = exc
                if attempt >= 2:
                    logger.exception("openai.timeout.exhausted", attempts=attempt + 1)
                    raise LLMError(f"OpenAI timeout (3 retry-a): {exc}") from exc
                logger.warning("openai.timeout.retry", attempt=attempt + 1)
                await asyncio.sleep(2 ** attempt)
                continue
            except OpenAIError as exc:
                logger.exception("openai.api.error")
                raise LLMError(f"OpenAI API greška: {exc}") from exc

        raise LLMError(f"OpenAI nepoznata greška: {last_exc}")


def _get_retry_after(exc: RateLimitError) -> float | None:

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
