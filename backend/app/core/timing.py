"""Context manager za mjerenje latencije pojedinih faza.

Evaluacija u Fazi 4 zahtjeva mjerenje latencije po fazama:
LLM generation ms / validation ms / execution ms / total ms. Ručno
pozivanje ``time.perf_counter()`` na više mjesta je bug-ant i lako se
zaboravi — context manager čini mjerenje teško zaboravnim.

Korištenje:

    with Timer() as t:
        result = await provider.generate(prompt)
    logger.info("llm.done", elapsed_ms=t.elapsed_ms)
"""

from __future__ import annotations

import time
from types import TracebackType


class Timer:
    """Mjeri vrijeme u milisekundama između ulaska i izlaska iz ``with``."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self._end: float | None = None

    def __enter__(self) -> Timer:
        # perf_counter je monotoni timer s najvećom dostupnom rezolucijom —
        # idealno za mjerenje malih vremenskih razlika (umjesto time.time()
        # koji može skočiti pri NTP sinkronizaciji).
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._end = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        """Proteklo vrijeme u milisekundama."""

        end = self._end if self._end is not None else time.perf_counter()
        return (end - self._start) * 1000.0
