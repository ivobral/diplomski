"""End-to-end /api/query test s mock LLM + mock executor.

Cilj: dokazati da kompletan tijek (HTTP → handler → service → validator →
executor → response) radi i da blocked_reason putanja stiže do JSON-a.

Mock LLM provider vraća pre-definirani SQL po pitanju (mapping question →
sql), tako da test ne treba internet ni API ključ.

Mock executor vraća fiksne rezultate na uspješan SQL.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_llm_provider,
    get_prompt_builder,
    get_query_executor,
    get_query_service,
    get_retry_engine,
    get_validator,
)
from app.db.schema_inspector import DatabaseSchema
from app.llm.base import BaseLLMProvider, LLMResponse, Prompt
from app.main import app
from app.services.execution_service import ExecutionResult
from app.services.query_service import QueryService
from app.services.retry_engine import RetryEngine
from app.validation.validator import SqlValidator

# ----------------------------------------------------------------------
# Mock objekti
# ----------------------------------------------------------------------


class MockLLM(BaseLLMProvider):
    """LLM koji vraća fiksne SQL string-ove po keyword-ima u pitanju.

    Ne pravi HTTP poziv — instant response, deterministički, no API key needed.
    """

    def __init__(self, responses: dict[str, str]) -> None:
        # responses: question keyword → SQL string
        self._responses = responses

    def name(self) -> str:
        return "mock"

    async def generate(self, prompt: Prompt) -> LLMResponse:
        # Naivno keyword matching nad user prompt-om
        text = prompt.user.lower()
        for keyword, sql in self._responses.items():
            if keyword in text:
                return LLMResponse(
                    sql=sql,
                    raw_text=sql,
                    model="mock",
                    latency_ms=1.0,
                    input_tokens=10,
                    output_tokens=5,
                )
        # Fallback — pitanje koje nije unaprijed mapirano
        return LLMResponse(
            sql="SELECT 1 AS unknown",
            raw_text="SELECT 1 AS unknown",
            model="mock",
            latency_ms=1.0,
            input_tokens=10,
            output_tokens=5,
        )


class MockExecutor:
    """Executor stub koji vraća fiksne rezultate — nema DB konekcija."""

    def __init__(self) -> None:
        self.called_with_sql: list[str] = []

    async def execute(self, sql: str) -> ExecutionResult:
        # Loggamo poziv za asserte u testovima
        self.called_with_sql.append(sql)
        return ExecutionResult(
            columns=["count"],
            rows=[[275]],
            row_count=1,
            execution_ms=1.0,
        )


# ----------------------------------------------------------------------
# Fixtures koje override-aju FastAPI DI
# ----------------------------------------------------------------------


@pytest.fixture
def mock_executor() -> MockExecutor:
    return MockExecutor()


@pytest.fixture
def client(stub_inspector, chinook_schema: DatabaseSchema, mock_executor: MockExecutor) -> TestClient:
    """FastAPI TestClient s overrideanim ovisnostima.

    Override-amo:
    - schema inspector (vraća Chinook hard-coded)
    - LLM provider (mock)
    - executor (mock)
    - validator i prompt_builder dobivaju mock inspector kroz constructor

    Service se sastavi ručno s tim mock-ovima.
    """

    inspector = stub_inspector(chinook_schema)
    mock_llm = MockLLM(
        responses={
            "how many artist": "SELECT COUNT(*) FROM artist",
            "drop": "DROP TABLE artist",  # za blocked test
            "delete": "DELETE FROM artist",  # za blocked test
        }
    )

    from app.llm.prompts.builder import PromptBuilder

    validator = SqlValidator(
        schema_inspector=inspector,
        default_limit=1000,
        default_dialect="postgres",
    )
    prompt_builder = PromptBuilder(schema_inspector=inspector, default_dialect="postgres")
    retry_engine = RetryEngine(provider=mock_llm, prompt_builder=prompt_builder, max_attempts=1)
    service = QueryService(
        schema_inspector=inspector,
        prompt_builder=prompt_builder,
        provider=mock_llm,
        validator=validator,
        executor=mock_executor,  # type: ignore[arg-type] — duck-typing OK za test
        retry_engine=retry_engine,
    )

    # Override FastAPI deps
    app.dependency_overrides[get_query_service] = lambda: service
    app.dependency_overrides[get_llm_provider] = lambda: mock_llm
    app.dependency_overrides[get_validator] = lambda: validator
    app.dependency_overrides[get_prompt_builder] = lambda: prompt_builder
    app.dependency_overrides[get_query_executor] = lambda: mock_executor
    app.dependency_overrides[get_retry_engine] = lambda: retry_engine

    yield TestClient(app)

    app.dependency_overrides.clear()


# ----------------------------------------------------------------------
# Testovi
# ----------------------------------------------------------------------


class TestHappyPath:
    def test_count_artists(self, client: TestClient, mock_executor: MockExecutor) -> None:
        response = client.post("/api/query", json={"question": "How many artists are in DB?"})
        assert response.status_code == 200
        data = response.json()
        assert data["validated"] is True
        assert data["executed"] is True
        assert data["row_count"] == 1
        assert data["rows"] == [[275]]
        assert data["blocked_reason"] is None
        # SQL je išao do executor-a
        assert any("artist" in sql.lower() for sql in mock_executor.called_with_sql)


class TestBlocked:
    def test_drop_table_blocked(self, client: TestClient, mock_executor: MockExecutor) -> None:
        response = client.post("/api/query", json={"question": "Drop the artist table"})
        assert response.status_code == 200
        data = response.json()
        assert data["blocked_reason"] is not None
        assert data["executed"] is False
        # Executor MORA NE BITI POZVAN za blocked upit
        assert len(mock_executor.called_with_sql) == 0

    def test_delete_blocked(self, client: TestClient, mock_executor: MockExecutor) -> None:
        response = client.post("/api/query", json={"question": "Delete all artists"})
        data = response.json()
        assert data["blocked_reason"] is not None
        assert data["executed"] is False
        assert len(mock_executor.called_with_sql) == 0


class TestStrategySelection:
    def test_strategy_a_passed_through(self, client: TestClient) -> None:
        """Strategy A: prompt builder ne dohvaća shemu."""

        response = client.post(
            "/api/query",
            json={"question": "How many artists are there?", "strategy": "A"},
        )
        assert response.status_code == 200
        # Pošto je mock LLM, ovo bi tre baciti normalno
        assert response.json()["validated"] is True

    def test_strategy_d_default(self, client: TestClient) -> None:
        response = client.post(
            "/api/query",
            json={"question": "How many artists?"},  # bez strategy → default D
        )
        assert response.status_code == 200


class TestValidation:
    def test_input_validation_empty_question(self, client: TestClient) -> None:
        """Pydantic validacija: min_length=1."""

        response = client.post("/api/query", json={"question": ""})
        assert response.status_code == 422  # Unprocessable

    def test_input_validation_too_long(self, client: TestClient) -> None:
        """Pydantic validacija: max_length=2000."""

        response = client.post("/api/query", json={"question": "a" * 3000})
        assert response.status_code == 422
