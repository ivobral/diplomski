"""DTO za rezultat SQL validacije — koristi se interno i u API responseu.

Validation rezultat namjerno je Pydantic model (a ne dataclass) jer ga
izravno serijaliziramo u API odgovor kad korisnik želi vidjeti detalje
zašto je upit odbijen.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    """Outcome SQL validation pipeline-a."""

    ok: bool
    normalized_sql: str | None = None
    errors: list[str] = Field(default_factory=list)
    # Razlog odbijanja — odvojen od `errors` jer označava SIGURNOSNU odluku
    # ("DROP TABLE nije dozvoljen"), dok su `errors` syntax/semantic problemi.
    blocked_reason: str | None = None
