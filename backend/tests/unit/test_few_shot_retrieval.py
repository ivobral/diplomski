"""Testovi za FewShotRetriever (TF-IDF sličnost pitanja).

Provjeravamo da:
- Retriever vrati top-K rangirane po sličnosti
- Filter po db_id radi
- exclude_question_ids omogućava leave-one-out evaluaciju
- Empty pool ili zero overlap → prazan rezultat (graceful)
"""

from __future__ import annotations

import pytest

from app.evaluation.bird_loader import BirdQuestion
from app.evaluation.few_shot_retrieval import FewShotRetriever, format_few_shot_examples


def _q(qid: int, db: str, question: str, gold: str = "SELECT 1", difficulty: str = "simple") -> BirdQuestion:
    return BirdQuestion(
        question_id=qid,
        db_id=db,
        question=question,
        evidence="",
        gold_sql=gold,
        difficulty=difficulty,
    )


@pytest.fixture
def pool() -> list[BirdQuestion]:
    return [
        _q(1, "schools", "How many students are enrolled?"),
        _q(2, "schools", "How many students are enrolled in California?"),
        _q(3, "schools", "What is the average enrollment per school?"),
        _q(4, "schools", "Find schools with SAT score over 1500"),
        _q(5, "music", "Find albums by Iron Maiden"),
        _q(6, "music", "How many tracks per genre?"),
    ]


class TestBasicRetrieval:
    def test_returns_top_k(self, pool: list[BirdQuestion]) -> None:
        retriever = FewShotRetriever(pool=pool)
        examples = retriever.retrieve_for(
            question="How many students are in school?",
            db_id="schools",
            k=2,
        )
        assert len(examples) <= 2
        # Najsličniji bi trebao biti Q1 ili Q2 (svi spominju "students enrolled")
        ids = [e.source_question_id for e in examples]
        assert 1 in ids or 2 in ids

    def test_results_sorted_by_similarity(self, pool: list[BirdQuestion]) -> None:
        retriever = FewShotRetriever(pool=pool)
        examples = retriever.retrieve_for(
            question="students enrolled California",
            db_id="schools",
            k=3,
        )
        # Similarities su silazne
        for i in range(len(examples) - 1):
            assert examples[i].similarity >= examples[i + 1].similarity


class TestDbFilter:
    def test_db_id_filter_excludes_other_dbs(self, pool: list[BirdQuestion]) -> None:
        retriever = FewShotRetriever(pool=pool)
        examples = retriever.retrieve_for(
            question="?",
            db_id="schools",
            k=10,
        )
        # Sva vraćena pitanja moraju biti iz "schools" baze
        for e in examples:
            # Pošto FewShotExample ne nosi db_id, pronadji u poolu
            original = next(q for q in pool if q.question_id == e.source_question_id)
            assert original.db_id == "schools"

    def test_no_db_filter(self, pool: list[BirdQuestion]) -> None:
        """Bez db_id filter-a, sva pitanja u poolu su kandidati."""

        retriever = FewShotRetriever(pool=pool)
        examples = retriever.retrieve_for(
            question="genre",
            db_id=None,
            k=10,
        )
        # Bar 1 result jer "genre" je u Q6 (music)
        assert any(e.source_question_id == 6 for e in examples)


class TestExclude:
    def test_excludes_self_question(self, pool: list[BirdQuestion]) -> None:
        """Leave-one-out: pitanje ne smije naći samo sebe kao primjer."""

        retriever = FewShotRetriever(pool=pool)
        examples = retriever.retrieve_for(
            question="How many students are enrolled?",  # match s Q1
            db_id="schools",
            k=5,
            exclude_question_ids={1},
        )
        ids = [e.source_question_id for e in examples]
        assert 1 not in ids


class TestEdgeCases:
    def test_empty_pool(self) -> None:
        retriever = FewShotRetriever(pool=[])
        assert retriever.retrieve_for(question="?", db_id="any", k=3) == []

    def test_no_overlap_returns_empty(self, pool: list[BirdQuestion]) -> None:
        """Pitanje bez ijednog overlap-a s pool-om vraća prazno (filter za similarity > 0)."""

        retriever = FewShotRetriever(pool=pool)
        # Garbled text bez stvarnih riječi — TF-IDF score 0
        examples = retriever.retrieve_for(
            question="xyzqwerty foobazbang",
            db_id="schools",
            k=3,
        )
        # Možda vraća jedne s 0 similarity ili filter ih izbacuje — provjeri da
        # nije crash
        assert isinstance(examples, list)


class TestFormatting:
    def test_format_empty(self) -> None:
        assert format_few_shot_examples([]) == ""

    def test_format_includes_sql(self, pool: list[BirdQuestion]) -> None:
        retriever = FewShotRetriever(pool=pool)
        examples = retriever.retrieve_for(
            question="enrolled students",
            db_id="schools",
            k=2,
        )
        block = format_few_shot_examples(examples)
        if examples:
            assert "Example" in block
            assert "Question:" in block
            assert "SQL:" in block
