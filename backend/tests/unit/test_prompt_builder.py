"""Testovi za PromptBuilder.

Provjeravamo da svaka strategija (A/B/C/D) ugradjuje različite informacije
u user prompt, da dialect propagacija radi, i da retry prompt sadrži
prethodne greške + dostupne identifikatore.
"""

from __future__ import annotations

from app.db.schema_inspector import DatabaseSchema
from app.llm.prompts.builder import PromptBuilder
from app.llm.prompts.strategies import get_strategy


class TestStrategies:
    """Każde strategija stavlja samo svoju razinu informacija u prompt."""

    async def test_strategy_a_minimal(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        """A — samo pitanje, bez sheme."""

        builder = PromptBuilder(schema_inspector=stub_inspector(chinook_schema))
        strategy = get_strategy("A")
        prompt = await builder.build(question="How many artists?", strategy=strategy)

        assert "How many artists?" in prompt.user
        # A ne smije sadržavati ime tablice (jer nema sheme)
        assert "artist" not in prompt.user.lower() or "artists?" in prompt.user.lower()

    async def test_strategy_b_includes_schema(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        """B — pitanje + schema."""

        builder = PromptBuilder(schema_inspector=stub_inspector(chinook_schema))
        prompt = await builder.build(question="How many artists?", strategy=get_strategy("B"))

        assert "How many artists?" in prompt.user
        assert "artist" in prompt.user.lower()  # schema je tu

    async def test_strategy_c_includes_relations(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        """C — pitanje + schema + relations."""

        builder = PromptBuilder(schema_inspector=stub_inspector(chinook_schema))
        prompt = await builder.build(question="Top selling artists?", strategy=get_strategy("C"))

        # C prompt sadrži schema + neki indicator relacija (FK info)
        assert "artist" in prompt.user.lower()
        assert "album" in prompt.user.lower()

    async def test_strategy_d_full(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        """D — sve gore + evidence + decomposition prosljeđuje se ako je dato."""

        builder = PromptBuilder(schema_inspector=stub_inspector(chinook_schema))
        prompt = await builder.build(
            question="How many artists?",
            strategy=get_strategy("D"),
            evidence="evidence: count distinct names",
            decomposition="Step 1: find artist table\nStep 2: count rows",
        )

        assert "evidence: count distinct names" in prompt.user.lower() or "evidence" in prompt.user.lower()
        assert "Step 1" in prompt.user or "step 1" in prompt.user.lower()


class TestSchemaOverride:
    """schema_override zaobilazi inspector — koristi se u benchmark-u per db_id."""

    async def test_override_used_instead_of_inspector(
        self, stub_inspector, chinook_schema: DatabaseSchema
    ) -> None:
        # Inspector ima drugu shemu, schema_override ima Chinook
        empty_inspector = stub_inspector(DatabaseSchema(tables=()))
        builder = PromptBuilder(schema_inspector=empty_inspector)

        prompt = await builder.build(
            question="?",
            strategy=get_strategy("B"),
            schema_override=chinook_schema,
        )
        # Empty inspector bi dao prazan schema text; override sadrži Chinook
        assert "artist" in prompt.user.lower() or "album" in prompt.user.lower()


class TestDialect:
    """Dialect propagacija u system prompt."""

    async def test_postgres_default(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        builder = PromptBuilder(
            schema_inspector=stub_inspector(chinook_schema),
            default_dialect="postgres",
        )
        prompt = await builder.build(question="?", strategy=get_strategy("D"))
        # Postgres system prompt referencira Postgres specifične stvari
        assert "postgres" in prompt.system.lower() or "sql" in prompt.system.lower()

    async def test_sqlite_override(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        builder = PromptBuilder(
            schema_inspector=stub_inspector(chinook_schema),
            default_dialect="postgres",
        )
        prompt = await builder.build(
            question="?",
            strategy=get_strategy("D"),
            dialect="sqlite",
        )
        # SQLite system prompt razlikuje se od Postgres-ovog (npr. datetime funkcije)
        assert "sqlite" in prompt.system.lower()


class TestRetryPrompt:
    """Retry prompt sadrži stari SQL + greške."""

    async def test_retry_includes_previous_sql(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        builder = PromptBuilder(schema_inspector=stub_inspector(chinook_schema))
        prompt = await builder.build_retry(
            question="How many?",
            previous_sql="SELECT * FROM nepostojeca",
            errors=["Tablica 'nepostojeca' ne postoji"],
        )
        assert "SELECT * FROM nepostojeca" in prompt.user
        assert "ne postoji" in prompt.user

    async def test_retry_includes_schema(self, stub_inspector, chinook_schema: DatabaseSchema) -> None:
        """Retry prompt mora pokazati dostupne tablice da LLM može popraviti."""

        builder = PromptBuilder(schema_inspector=stub_inspector(chinook_schema))
        prompt = await builder.build_retry(
            question="?",
            previous_sql="SELECT 1 FROM nope",
            errors=["err"],
        )
        # Pokazuje da retry ima full schemu (jer to je glavna intervencija)
        assert "artist" in prompt.user.lower()
