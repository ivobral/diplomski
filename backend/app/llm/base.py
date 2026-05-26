"""LLM provider abstraction — bazna klasa i zajednički DTO-i.

Svaki konkretni provider (Anthropic, OpenAI, Ollama) nasljeđuje
``BaseLLMProvider`` i implementira ``async generate(prompt) -> LLMResponse``.
Ovaj sloj namjerno NE koristi nikakav wrapper-framework (LangChain,
LlamaIndex) — izravni SDK pozivi u konkretnim provider klasama.

Sve što provideri trebaju imati zajedničko (struktura prompta, oblik
odgovora, ekstrakcija SQL-a iz LLM-ovog teksta) živi ovdje da ne
ponavljamo logiku u svakom konkretnom provideru.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Prompt:
    """Strukturirani prompt koji se šalje LLM-u.

    Razdvojen je u ``system`` (uvijek prisutan, sadrži pravila i ton) i
    ``user`` (varijabilni dio — pitanje + shema + eventualno retry kontekst).
    Provideri sami znaju kako prevesti ovo u svoj SDK-specifični format
    (Anthropic koristi ``system=`` parametar, OpenAI prvu poruku u listi
    s rolom "system", Ollama isto).
    """

    system: str
    user: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Outcome jednog poziva LLM-u.

    Polja ``input_tokens``/``output_tokens`` su ``None`` ako provider ne
    izvještava korištenje tokena (npr. neki Ollama backend-i). Polje
    ``sql`` sadrži već očišćen SQL (nakon ``extract_sql``), dok je
    ``raw_text`` originalan odgovor — koristan za debugging i evaluaciju.
    """

    sql: str
    raw_text: str
    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None


class BaseLLMProvider(ABC):
    """Apstraktni roditelj svih LLM providera."""

    @abstractmethod
    async def generate(self, prompt: Prompt) -> LLMResponse:
        """Pošalji prompt i vrati strukturiran odgovor.

        Konkretni provider mjeri latenciju, zove SDK, extracta SQL iz teksta
        i vraća ``LLMResponse``. Iznimke iz SDK-a treba zaviti u
        ``LLMError`` (iz ``app.core.exceptions``) — više slojeve onda
        hvataju jednu domain iznimku, neovisno o provideru.
        """

    @abstractmethod
    def name(self) -> str:
        """Kratko ime providera za logiranje (npr. ``"anthropic"``)."""


# ----------------------------------------------------------------------
# SQL extraction helper
# ----------------------------------------------------------------------

# Markdown code-block obrazac koji LLM-ovi često koriste unatoč uputi da to
# ne rade: ```sql\n<sql>\n``` ili ```\n<sql>\n```. Greedy match na sadržaj
# između otvarača i zatvarača, s preferiranjem prvog zatvarača (lazy `.*?`).
_CODE_BLOCK_RE = re.compile(
    r"```(?:sql)?\s*\n(.+?)\n```",
    re.IGNORECASE | re.DOTALL,
)


def extract_sql(raw_text: str) -> str:
    """Izvuče čisti SQL string iz LLM-ovog teksta.

    LLM-ovi često obasvavaju SQL u code-blokove ili dodaju kratko
    objašnjenje prije/poslije, čak i kad im se eksplicitno kaže da to ne
    rade. Helper pokušava sljedećim redom:

    1. Ako postoji prvi ```sql``` ili ``` ``` blok, vrati njegov sadržaj.
    2. Inače, ako tekst počinje s razumljivim SQL keyword-om
       (SELECT / WITH), vrati cijeli tekst.
    3. Inače pokušaj naći prvi SELECT/WITH u tekstu i vrati od tamo nadalje.

    Trailing semicolon, vodeći i prateći whitespace se uklanjaju jer
    bi multi-statement check kasnije inače pao na trivijalnom završnom ``;``.
    """

    text = raw_text.strip()

    # Step 1: markdown code-block (najčešći slučaj).
    match = _CODE_BLOCK_RE.search(text)
    if match:
        candidate = match.group(1).strip()
        return _strip_trailing_semicolon(candidate)

    # Step 2: tekst počinje SQL keyword-om — uzmi sve kako je.
    if re.match(r"^\s*(SELECT|WITH)\b", text, re.IGNORECASE):
        return _strip_trailing_semicolon(text)

    # Step 3: nađi prvi SELECT/WITH u tekstu i vrati od tamo do kraja.
    fallback = re.search(r"(SELECT|WITH)\b.*", text, re.IGNORECASE | re.DOTALL)
    if fallback:
        return _strip_trailing_semicolon(fallback.group(0).strip())

    # Step 4: ništa ne pomaže — vrati originalni tekst. Validator će ga
    # odbiti s jasnijom porukom.
    return _strip_trailing_semicolon(text)


def _strip_trailing_semicolon(sql: str) -> str:
    """Ukloni završni ``;`` (ali ne unutarnje — to bi bio multi-statement)."""

    return sql.rstrip().rstrip(";").rstrip()
