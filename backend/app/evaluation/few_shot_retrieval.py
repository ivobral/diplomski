"""Retrieval-augmented few-shot prompting.

Akademska referenca: Gao et al. 2023 (DAIL-SQL) — top boost na BIRD-u (++5%
nad fixed few-shot). Koncept: umjesto fiksnih primjera u system promptu,
za svako novo pitanje **dinamički dohvati** sličnih pitanja iz pool-a
training pitanja, pa ih koristi kao kontekst.

Naša implementacija koristi:
- **TF-IDF** sličnost (jednostavnije od embeddings — bez novog API poziva
  ili dodatnog modela). Dovoljna za naš scope; embeddings bi dali +1-2pp
  ali kompliciraju arhitekturu.
- **Per-database pool**: dohvaćamo primjere iz iste BIRD baze kao current
  question. To je rigoroznije (different DBs imaju različite sheme; primjeri
  iz druge baze nisu relevantni).
- **Leave-one-out**: BIRD Mini-Dev je naš jedini dataset (nema train);
  pri evaluaciji jednog pitanja, ostatak Mini-Dev-a služi kao "training pool"
  za retrieval. Akademski je defendable (nego literatura preferira poseban
  train set, što bismo trebali napomenuti u radu).

API:
    retriever = FewShotRetriever(all_questions)
    examples = retriever.retrieve_for(question, db_id, k=3, exclude_id=23)
    # Returns: list[(question, gold_sql)]
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.evaluation.bird_loader import BirdQuestion

_TOKEN_RE = re.compile(r"\b\w+\b")


def _tokenize(text: str) -> list[str]:
    """Jednostavan tokenizator: lowercased word tokens."""

    return _TOKEN_RE.findall(text.lower())


@dataclass(slots=True)
class FewShotExample:
    """Jedan retrieved primjer za uvrstavanje u prompt."""

    question: str
    gold_sql: str
    similarity: float
    source_question_id: int


class FewShotRetriever:
    """TF-IDF retriever za BIRD-style pitanja.

    Indeks gradi pri konstrukciji; svaki ``retrieve_for`` poziv je O(N) kroz
    sve pool entries, što je za BIRD-Mini (500 entries) ispod 50ms.
    Embedding-based retriever bi bio brži za veliki korpus, ali za diplomski
    scope TF-IDF je dovoljan i interpretabilan.
    """

    def __init__(self, pool: list[BirdQuestion]) -> None:
        self._pool = pool
        # Pre-compute token Counter-e + document frequencies za IDF
        self._doc_tokens: list[Counter[str]] = []
        df: Counter[str] = Counter()
        for q in pool:
            tokens = _tokenize(q.question)
            counter = Counter(tokens)
            self._doc_tokens.append(counter)
            # DF — broj dokumenata u kojima riječ pojavljuje (ne ukupno)
            df.update(set(tokens))

        n_docs = max(1, len(pool))
        # IDF: log(N / (1 + df)) + 1 (smoothed)
        self._idf: dict[str, float] = {
            term: math.log(n_docs / (1 + count)) + 1.0
            for term, count in df.items()
        }

    def retrieve_for(
        self,
        question: str,
        db_id: str | None = None,
        k: int = 3,
        exclude_question_ids: set[int] | None = None,
    ) -> list[FewShotExample]:
        """Dohvati top-K sličnih primjera iz pool-a.

        Args:
            question: novo pitanje za koje tražimo primjere.
            db_id: ako dat, filter na isti BIRD database (preporučeno —
                različite baze imaju različite sheme, irelevantni primjeri).
            k: broj primjera za vratiti.
            exclude_question_ids: skup question_id-eva koje izostaviti
                (npr. trenutno pitanje da nije svoj primjer — leave-one-out).

        Returns:
            Lista ``FewShotExample`` sortirana od najsličnijeg. Manje od K
            ako pool nakon filter-a nije dovoljno velik.
        """

        exclude = exclude_question_ids or set()

        query_tokens = _tokenize(question)
        query_vec = self._build_query_vector(query_tokens)
        query_norm = math.sqrt(sum(v * v for v in query_vec.values()))
        if query_norm == 0:
            return []

        results: list[tuple[float, int]] = []  # (similarity, pool_index)

        for idx, (pool_q, doc_counter) in enumerate(zip(self._pool, self._doc_tokens, strict=True)):
            if pool_q.question_id in exclude:
                continue
            if db_id is not None and pool_q.db_id != db_id:
                continue

            doc_vec = self._build_doc_vector(doc_counter)
            doc_norm = math.sqrt(sum(v * v for v in doc_vec.values()))
            if doc_norm == 0:
                continue

            # Cosine similarity nad rijetkim vektorima
            shared = set(query_vec) & set(doc_vec)
            dot = sum(query_vec[t] * doc_vec[t] for t in shared)
            similarity = dot / (query_norm * doc_norm)
            results.append((similarity, idx))

        results.sort(reverse=True)
        top = results[:k]

        return [
            FewShotExample(
                question=self._pool[idx].question,
                gold_sql=self._pool[idx].gold_sql,
                similarity=sim,
                source_question_id=self._pool[idx].question_id,
            )
            for sim, idx in top
            if sim > 0  # filter zero-similarity (nema overlap-a uopće)
        ]

    def _build_query_vector(self, tokens: list[str]) -> dict[str, float]:
        """TF-IDF vektor za query: tf * idf, tf je term frequency."""

        if not tokens:
            return {}
        tf: Counter[str] = Counter(tokens)
        total = sum(tf.values())
        return {
            term: (count / total) * self._idf.get(term, 1.0)
            for term, count in tf.items()
        }

    def _build_doc_vector(self, doc_counter: Counter[str]) -> dict[str, float]:
        """TF-IDF vektor za document; tf je broj pojavljivanja kroz duljinu."""

        total = sum(doc_counter.values())
        if total == 0:
            return {}
        return {
            term: (count / total) * self._idf.get(term, 1.0)
            for term, count in doc_counter.items()
        }


def format_few_shot_examples(examples: list[FewShotExample]) -> str:
    """Formatira retrieved primjere za uvrstavanje u prompt.

    Vraća tekstualni blok spreman za prepend ispred glavnog pitanja.
    Prazan string ako nema primjera.
    """

    if not examples:
        return ""

    lines = ["Similar examples from the same database:"]
    for i, ex in enumerate(examples, 1):
        lines.append(f"\nExample {i}:")
        lines.append(f"Question: {ex.question}")
        lines.append(f"SQL:\n{ex.gold_sql}")
    lines.append("")
    return "\n".join(lines)
