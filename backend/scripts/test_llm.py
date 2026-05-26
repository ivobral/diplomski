"""Smoke test za LLM Provider layer + Prompt Builder.

Šalje jednostavno pitanje LLM-u i ispisuje generirani SQL — služi kao
"je li sve spojeno" provjera kad korisnik popuni .env s API ključem.

Pokretanje (unutar backend kontejnera, nakon postavljanja .env):

    docker compose exec backend python scripts/test_llm.py
"""

from __future__ import annotations

import asyncio
import sys

from app.api.deps import get_llm_provider, get_prompt_builder
from app.llm.prompts.strategies import get_strategy


async def main() -> int:
    try:
        provider = get_llm_provider()
    except Exception as exc:
        print(f"FAIL: provider ne može biti instanciran: {exc}")
        print("Provjeri da je .env popunjen za odabrani LLM_PROVIDER.")
        return 1

    print(f"Provider: {provider.name()}")

    builder = get_prompt_builder()

    # Sve 4 strategije — vidiš kako rastu promptovi.
    for code in ("A", "B", "C", "D"):
        strategy = get_strategy(code)
        prompt = await builder.build("How many artists are in the database?", strategy)
        print()
        print(f"--- Strategija {code} | user prompt (prvih 400 znakova) ---")
        print(prompt.user[:400] + ("..." if len(prompt.user) > 400 else ""))

    # Stvarni LLM poziv samo s default (D) strategijom — jeftino, dokazuje
    # da provider radi end-to-end.
    print()
    print("--- LLM poziv (strategija D) ---")
    strategy = get_strategy("D")
    prompt = await builder.build("How many artists are in the database?", strategy)
    response = await provider.generate(prompt)
    print(f"Model: {response.model}")
    print(f"Latency: {response.latency_ms:.0f} ms")
    print(f"Tokens: input={response.input_tokens} output={response.output_tokens}")
    print(f"Raw text: {response.raw_text[:300]}")
    print(f"Extracted SQL: {response.sql}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
