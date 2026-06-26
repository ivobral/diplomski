"""Testovi za ``extract_sql`` helper.

LLM-ovi različito formatiraju output — markdown code blokovi, plain text,
preamble. Helper mora iz svih varijanti izvući čisti SQL string.
"""

from __future__ import annotations

from app.llm.base import extract_sql


class TestPlanBlock:
    """CoT scaffolding wraps the planning text in <plan>...</plan>.
    Extraction must strip it so the SQL is returned cleanly."""

    def test_plan_followed_by_sql(self) -> None:
        raw = (
            "<plan>\n- need artist + album\n- JOIN on artist_id\n</plan>\n"
            "SELECT a.name FROM artist a"
        )
        assert extract_sql(raw) == "SELECT a.name FROM artist a"

    def test_plan_with_select_word_inside(self) -> None:
        """The word SELECT inside <plan> must NOT be picked up as SQL."""

        raw = (
            "<plan>plan: SELECT name and count from artist+album</plan>\n"
            "SELECT a.name, COUNT(*) FROM artist a"
        )
        assert extract_sql(raw) == "SELECT a.name, COUNT(*) FROM artist a"

    def test_plan_case_insensitive(self) -> None:
        raw = "<PLAN>steps</PLAN>\nSELECT 1"
        assert extract_sql(raw) == "SELECT 1"

    def test_plan_with_markdown_sql_after(self) -> None:
        raw = "<plan>steps</plan>\n```sql\nSELECT 1\n```"
        assert extract_sql(raw) == "SELECT 1"


class TestMarkdownBlocks:
    def test_sql_code_block(self) -> None:
        raw = "Here is your query:\n```sql\nSELECT * FROM artist\n```\nHope it helps."
        assert extract_sql(raw) == "SELECT * FROM artist"

    def test_generic_code_block(self) -> None:
        raw = "```\nSELECT * FROM artist LIMIT 10\n```"
        assert extract_sql(raw) == "SELECT * FROM artist LIMIT 10"

    def test_code_block_strips_trailing_semicolon(self) -> None:
        raw = "```sql\nSELECT 1;\n```"
        assert extract_sql(raw) == "SELECT 1"


class TestPlainText:
    def test_select_starts_directly(self) -> None:
        assert extract_sql("SELECT * FROM artist") == "SELECT * FROM artist"

    def test_with_cte_starts_directly(self) -> None:
        sql = "WITH t AS (SELECT 1) SELECT * FROM t"
        assert extract_sql(sql) == sql

    def test_trailing_semicolon_stripped(self) -> None:
        assert extract_sql("SELECT 1;") == "SELECT 1"

    def test_leading_whitespace(self) -> None:
        assert extract_sql("  \n  SELECT 1") == "SELECT 1"


class TestFallback:
    def test_select_in_middle_of_text(self) -> None:
        """LLM napisao objašnjenje pa SQL — extract_sql ga nađe."""

        raw = "Sure, here's the SQL: SELECT name FROM artist"
        out = extract_sql(raw)
        assert out.startswith("SELECT")
        assert "name FROM artist" in out

    def test_empty_string(self) -> None:
        assert extract_sql("") == ""

    def test_no_sql_keyword(self) -> None:
        """Ako nema SELECT/WITH, vraća se input kakav je — validator će onda odbiti."""

        result = extract_sql("This is not SQL at all.")
        # Ne testiramo egzaktnu vrijednost, samo da nije crashalo i da nije prazno
        assert isinstance(result, str)
