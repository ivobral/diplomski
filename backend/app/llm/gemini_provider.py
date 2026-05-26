"""Google Gemini LLM provider — REST API kroz httpx (bez Google SDK-a).

Razlog izbjegavanja službenog ``google-genai`` SDK-a: dodaje teški
google-* dependency lanac, koji se brzo mijenja, i ne nudi nam ništa
što ne možemo direktno preko REST-a. ``httpx.AsyncClient`` je dovoljan i
dosljedan s našim ``OllamaProvider`` pristupom.

Konfiguracija:
- ``GEMINI_API_KEY`` — ključ iz Google AI Studio
  (https://aistudio.google.com/apikey)
- ``GEMINI_MODEL``   — npr. ``gemini-2.0-flash``, ``gemini-2.5-flash``,
  ``gemini-2.5-pro``. Točan naziv provjeri u dokumentaciji modela.

Free tier (provjereno u trenutku pisanja, vrijednosti su orijentir):
- ~15 zahtjeva/min
- ~1500 zahtjeva/dan
- ~1M tokena/min
Bez kartice, bez billinga.

Endpoint:
    POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
    ?key={GEMINI_API_KEY}
"""

from __future__ import annotations

import asyncio
import re

import httpx

from app.config import settings
from app.core.exceptions import ConfigurationError, LLMError
from app.core.logging import get_logger
from app.core.timing import Timer
from app.llm.base import BaseLLMProvider, LLMResponse, Prompt, extract_sql

logger = get_logger(__name__)

# Endpoint Google AI Studio. v1beta je trenutni stabilni endpoint za
# generateContent API; ako Google ikada promijeni, mijenja se ovdje.
_GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Maksimalan broj retry-a kad Gemini vrati 429 (rate limit). Free tier je
# 5 RPM, pa benchmark s 10+ pitanja predvidljivo "udari u zid" — automatski
# čekamo Google-ov "Please retry in N seconds" hint i probamo opet.
_MAX_RATE_LIMIT_RETRIES = 5


class GeminiProvider(BaseLLMProvider):
    """Provider za Google Gemini familiju modela."""

    def __init__(self, model: str | None = None) -> None:
        """Konstruktor s opcionalnim model override-om.

        Args:
            model: Ako je dato, koristi se umjesto ``settings.GEMINI_MODEL``.
                Koristi se iz CLI runnera za per-run promjenu bez restartanja.
                None → fallback na env varijablu.
        """

        if not settings.GEMINI_API_KEY:
            raise ConfigurationError(
                "GEMINI_API_KEY nije postavljen u .env. "
                "Pribavi besplatan ključ na https://aistudio.google.com/apikey."
            )
        effective_model = model or settings.GEMINI_MODEL
        if not effective_model:
            raise ConfigurationError(
                "GEMINI_MODEL nije postavljen (ni u .env ni preko model parametra). "
                "Provjeri dostupne modele na https://ai.google.dev/gemini-api/docs/models "
                "i upiši točno ime (npr. gemini-2.0-flash)."
            )
        self._api_key = settings.GEMINI_API_KEY
        self._model = effective_model
        # Gemini je obično brz (1-5s); 60s daje rezerve za prvi zahtjev /
        # spore mreže. Nema potrebe za 5-min timeout-om kao kod Ollama.
        self._timeout = httpx.Timeout(60.0, connect=10.0)

    def name(self) -> str:
        return "gemini"

    async def generate(self, prompt: Prompt) -> LLMResponse:
        # Gemini API ima zaseban ``systemInstruction`` field — naš ``Prompt``
        # već razdvaja sustav i user, što se savršeno mapira.
        # ``temperature: 0`` za determinizam (isti razlog kao kod ostalih providera).
        payload: dict = {
            "systemInstruction": {"parts": [{"text": prompt.system}]},
            "contents": [
                {"role": "user", "parts": [{"text": prompt.user}]},
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 1024,
            },
        }

        url = f"{_GEMINI_API_BASE}/models/{self._model}:generateContent"
        # API key ide kao query param (Google standard) ili header
        # ``x-goog-api-key``. Koristimo header — manje izlaganja key-a u
        # URL-u (URL-ovi se češće logiraju nego headeri).
        headers = {"x-goog-api-key": self._api_key}

        with Timer() as t:
            data = await self._post_with_rate_limit_retry(url, payload, headers)

        # Gemini struktura: data.candidates[0].content.parts[*].text — spajamo
        # sve text dijelove (obično je samo jedan).
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini nije vratio nijedan candidate. Odgovor: {data}")

        first = candidates[0]
        parts = first.get("content", {}).get("parts") or []
        raw_text = "".join(p.get("text", "") for p in parts).strip()
        sql = extract_sql(raw_text)

        usage = data.get("usageMetadata") or {}

        return LLMResponse(
            sql=sql,
            raw_text=raw_text,
            model=self._model,
            latency_ms=t.elapsed_ms,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )

    async def _post_with_rate_limit_retry(
        self, url: str, payload: dict, headers: dict
    ) -> dict:
        """Post na Gemini s automatskim retry-em na HTTP 429.

        Google free tier vraća poruku tipa "Please retry in 16.249s" — parsiramo
        broj i sleep-amo, zatim retry. Ako bez hinta, koristimo exponential
        backoff (1, 2, 4, 8, 16s).
        """

        last_exc: Exception | None = None
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                # Pokušaj izvući strukturiranu poruku greške.
                detail = ""
                try:
                    err_json = exc.response.json()
                    detail = err_json.get("error", {}).get("message", "")
                except Exception:
                    detail = exc.response.text[:200]

                if exc.response.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
                    # Pokušaj parsirati "Please retry in N seconds" hint;
                    # fallback na exponential backoff (2 ** attempt).
                    wait_s = _parse_retry_hint(detail) or (2 ** attempt)
                    logger.warning(
                        "gemini.rate_limit.retry",
                        attempt=attempt + 1,
                        max=_MAX_RATE_LIMIT_RETRIES,
                        wait_s=wait_s,
                        detail=detail[:120],
                    )
                    await asyncio.sleep(wait_s + 0.5)  # +0.5s sigurnosna margina
                    continue

                logger.exception("gemini.http.error", status=exc.response.status_code)
                raise LLMError(
                    f"Gemini API greška (HTTP {exc.response.status_code}): {detail}"
                ) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.exception("gemini.http.error")
                raise LLMError(f"Gemini API mrežna greška: {exc}") from exc

        # Iscrpljeni svi pokušaji
        raise LLMError(
            f"Gemini API i dalje rate-limited nakon {_MAX_RATE_LIMIT_RETRIES} retry-a: {last_exc}"
        )


def _parse_retry_hint(error_detail: str) -> float | None:
    """Vrati broj sekundi iz Google poruke "Please retry in 16.249s." ili None."""

    # Hint forma: "Please retry in 16.249351799s." ili "Please retry in 16s."
    match = re.search(r"retry in (\d+(?:\.\d+)?)s", error_detail)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None
