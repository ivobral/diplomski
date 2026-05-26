"""OpenAI-kompatibilni LLM provider.

Koristi službeni ``openai`` Python SDK (async ``AsyncOpenAI``) i podržava
**bilo koji** OpenAI-kompatibilni endpoint preko ``OPENAI_BASE_URL``:
- službeni OpenAI (``OPENAI_BASE_URL`` prazan → default ``api.openai.com``)
- GitHub Models (``https://models.github.ai/inference``)
- OpenRouter   (``https://openrouter.ai/api/v1``)
- Groq         (``https://api.groq.com/openai/v1``)
- lokalni vllm / llama.cpp / itd.

Konfiguracija:
- ``OPENAI_API_KEY`` — ključ od odabranog providera (može biti GitHub PAT
  za GitHub Models)
- ``OPENAI_MODEL``   — točan naziv modela kod tog providera
- ``OPENAI_BASE_URL`` (opcionalno) — endpoint URL kompatibilnog servisa

Defensive ponašanje pri error-ima:
- **429 Rate Limit**: automatski retry s exponential backoff (default 5 puta).
  Štiti benchmark od pojedinačnih burst-ova.
- **max_tokens** ograničeno na 1024 — sprječava skupe halucinacije s dugim
  output-om (npr. LLM odluči pisati esej umjesto SQL-a).
- **Timeout** 60s na svaki poziv — sprječava visećih request-a.
"""

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

# Max retry pokušaja na 429 / mrežni transient — paid tier OpenAI tipično
# ima 500+ RPM pa ovo retko treba, ali defensive vrijedi.
_MAX_RATE_LIMIT_RETRIES = 5

# Po-pozivu timeout u sekundama — paid OpenAI tipično odgovori za <3s,
# 60s ostavlja prostora za sporu mrežu / hot model load.
_REQUEST_TIMEOUT = 60.0


class OpenAIProvider(BaseLLMProvider):
    """Provider za OpenAI GPT modele kroz Chat Completions API."""

    def __init__(self, model: str | None = None) -> None:
        """Konstruktor s opcionalnim model override-om.

        Args:
            model: Ako je dato, koristi se umjesto ``settings.OPENAI_MODEL``.
                CLI runner-i koriste ovo za per-run promjenu modela bez
                restartanja backend kontejnera (npr. `--model gpt-4o-mini`).
                None → fallback na env varijablu (default ponašanje).
        """

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

        # OPENAI_BASE_URL omogućuje alt-endpoint (GitHub Models, OpenRouter,
        # Groq, lokalni vllm). Prazan string = service OpenAI.
        #
        # PAŽNJA: OpenAI SDK čita env var ``OPENAI_BASE_URL`` direktno i ne
        # tretira prazan string kao "default" — koristi ga doslovno kao
        # URL, što daje "Request URL missing protocol" grešku. Zato uvijek
        # eksplicitno prosljeđujemo URL (službeni default kad korisnik nije
        # postavio svoj).
        custom_base_url = settings.OPENAI_BASE_URL.strip()
        effective_base_url = custom_base_url or "https://api.openai.com/v1"

        self._client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=effective_base_url,
            timeout=httpx.Timeout(_REQUEST_TIMEOUT, connect=10.0),
            # SDK ima built-in retry — postavljamo na 0 jer mi sami handlamo
            # da imamo predvidljivo ponašanje i logiranje. SDK default je 2.
            max_retries=0,
        )
        self._model = effective_model
        self._base_url_label = custom_base_url or "openai-default"
        # Reasoning effort — koristi se samo za GPT-5 familiju i o-seriju.
        # Klasični modeli (gpt-4o, gpt-4o-mini) odbijaju ovaj parametar pa
        # ga šaljemo samo kad je eksplicitno postavljen u .env.
        self._reasoning_effort = settings.OPENAI_REASONING_EFFORT.strip() or None

    def name(self) -> str:
        # Ime sadrži i endpoint da se u logovima vidi koja API točka se koristi
        # (jer "openai" provider može biti GitHub Models, OpenRouter, itd.).
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
        """Pozovi Chat Completions s automatskim retry-em na 429.

        Backoff strategija: exponential počevši od 2s (2, 4, 8, 16, 32) +
        ``retry-after`` header ako je dat. To je standard OpenAI praksa.
        """

        last_exc: Exception | None = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                # Chat Completions API: prva poruka system rola sadrži pravila,
                # druga user rola sadrži konkretno pitanje + shemu.
                #
                # NAPOMENA o ``temperature``: GPT-5 i o-serija (reasoning
                # modeli) ne podržavaju temperature=0 — dopuštaju samo
                # default vrijednost (1). Zato ne prosljeđujemo parametar.
                #
                # ``max_completion_tokens`` zamjenjuje stari ``max_tokens``
                # za reasoning modele. Vrijednost 4096 — reasoning modeli
                # (GPT-5, o-serija) troše "thinking tokens" PRIJE finalnog
                # output-a, pa 1024 nije dovoljno za medium/high reasoning
                # + SQL response. 4096 ostavlja prostor za long reasoning +
                # tipičan SQL (~200-400 tokena finalni).
                #
                # ``reasoning_effort`` — opcionalno za GPT-5 / o-serija.
                # Smanjuje broj internih thinking tokena (manji trošak +
                # brža reakcija) za jednostavne taskove poput SQL gen.
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
                # Sve ostale OpenAI greške (auth, billing, model_not_found, …)
                # — nema smisla retry-ati, korisnik mora popraviti config.
                logger.exception("openai.api.error")
                raise LLMError(f"OpenAI API greška: {exc}") from exc

        # Defensive — ne bi se trebao izvršiti, ali za type-checker.
        raise LLMError(f"OpenAI nepoznata greška: {last_exc}")


def _get_retry_after(exc: RateLimitError) -> float | None:
    """Vrati `retry-after` header (sekunde) ako je dat u response-u."""

    try:
        # OpenAI SDK izlaže original response kroz exc.response
        response = getattr(exc, "response", None)
        if response is None:
            return None
        header = response.headers.get("retry-after")
        if header is None:
            return None
        return float(header)
    except (ValueError, AttributeError):
        return None
