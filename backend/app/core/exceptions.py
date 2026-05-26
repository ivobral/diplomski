"""Custom exception hijerarhija aplikacije.

Definicija eksplicitne hijerarhije iznimaka donosi dvije koristi:

1. **Granularno hvatanje** — pozivni sloj može uhvatiti ``ValidationError``
   različito od ``LLMError`` i prikazati korisniku različite poruke.
2. **Jasno semantičko značenje** — kod koji baca ``RetryExhaustedError``
   eksplicitno signalizira "iscrpili smo retry budžet", što generička
   ``Exception`` ne bi.

Sve iznimke nasljeđuju ``NL2SQLError`` da ih API exception handler može
hvatati zajednički i mapirati u prikladne HTTP statuse.
"""

from __future__ import annotations


class NL2SQLError(Exception):
    """Bazna iznimka — sve domain greške nasljeđuju nju."""


class ConfigurationError(NL2SQLError):
    """Konfiguracija je nevaljana (npr. nedostaje API ključ za odabrani provider)."""


class SchemaInspectionError(NL2SQLError):
    """Neuspjeh pri dohvatu sheme baze (DB nedostupan, prava nedostatna…)."""


class LLMError(NL2SQLError):
    """LLM provider nije uspio generirati odgovor (mreža, rate limit, auth)."""


class ValidationError(NL2SQLError):
    """Generirani SQL nije prošao validation pipeline (safety, syntax, semantic).

    Kada ova iznimka curi van retry engine-a, znači da niti popravak nije
    uspio i sustav je odustao od izvršavanja.
    """


class RetryExhaustedError(NL2SQLError):
    """Iscrpljen je budžet pokušaja u retry engine-u."""


class ExecutionError(NL2SQLError):
    """SQL je prošao validaciju, ali izvršavanje nad bazom je palo
    (timeout, runtime greška, connection problem)."""
