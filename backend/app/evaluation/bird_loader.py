"""BIRD Mini-Dev dataset loader.

BIRD Mini-Dev je standardni text-to-SQL benchmark koji koristi tisuće
istraživačkih radova. Naša verzija je 500-pitanjski subset (Mini-Dev) koji
omogućuje brze iteracije bez čekanja sat-cijela na cijeli BIRD prolaz.

Format pitanja (iz `mini_dev_sqlite.json`):

    {
      "question_id": 0,
      "db_id": "california_schools",
      "question": "What is the highest eligible free rate ...",
      "evidence": "Eligible free rate = ...",
      "SQL": "SELECT MAX(...) FROM ...",
      "difficulty": "simple" | "moderate" | "challenging"
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BirdQuestion:
    """Jedno BIRD pitanje s gold SQL-om."""

    question_id: int
    db_id: str
    question: str
    evidence: str   # human-written hint; eksperimentalno možemo uključiti ga u prompt
    gold_sql: str
    difficulty: str  # "simple" | "moderate" | "challenging"


class BirdLoader:
    """Učitava BIRD Mini-Dev pitanja iz lokalnog dataset-a."""

    def __init__(self, dataset_path: Path = Path("/app/data/bird_mini")) -> None:
        self._dataset_path = dataset_path
        self._questions_file = dataset_path / "questions.json"
        self._databases_dir = dataset_path / "databases"

    def is_ready(self) -> bool:
        """True ako je dataset preuzet i izgleda valjano."""

        return self._questions_file.exists() and self._databases_dir.is_dir()

    def db_path(self, db_id: str) -> Path:
        """Apsolutan put do SQLite file-a za zadanu BIRD bazu."""

        return self._databases_dir / db_id / f"{db_id}.sqlite"

    def load_questions(
        self,
        limit: int | None = None,
        difficulty: str | None = None,
    ) -> list[BirdQuestion]:
        """Vrati listu pitanja iz dataset-a.

        Args:
            limit: ako je dat, vrati samo prvih N pitanja (za pilot run-ove).
            difficulty: filter po BIRD difficulty (simple/moderate/challenging).

        Returns:
            Lista ``BirdQuestion`` objekata. Redoslijed slijedi questions.json
            (deterministički — sortiran po question_id).
        """

        if not self.is_ready():
            raise FileNotFoundError(
                f"BIRD Mini-Dev nije preuzet (očekujem {self._questions_file}). "
                f"Pokreni: docker compose exec backend python scripts/download_bird.py"
            )

        with self._questions_file.open(encoding="utf-8") as f:
            raw = json.load(f)

        questions: list[BirdQuestion] = []
        for entry in raw:
            # BIRD json schema je dosljedan ali tolerantno provjeravamo
            # ključeve — npr. neki dump-i koriste "SQL", neki "query".
            q = BirdQuestion(
                question_id=int(entry.get("question_id", -1)),
                db_id=str(entry["db_id"]),
                question=str(entry["question"]),
                evidence=str(entry.get("evidence", "")),
                gold_sql=str(entry.get("SQL") or entry.get("query", "")),
                difficulty=str(entry.get("difficulty", "unknown")),
            )

            if difficulty and q.difficulty != difficulty:
                continue
            questions.append(q)

        # Sortiraj po question_id za determinizam (BIRD json je obično već
        # sortiran, ali ne oslanjamo se).
        questions.sort(key=lambda q: q.question_id)

        if limit is not None:
            questions = questions[:limit]

        return questions

    def list_databases(self) -> list[str]:
        """Vrati popis db_id-eva za koje postoji SQLite file."""

        if not self._databases_dir.is_dir():
            return []
        return sorted(
            p.name for p in self._databases_dir.iterdir()
            if p.is_dir() and (p / f"{p.name}.sqlite").exists()
        )
