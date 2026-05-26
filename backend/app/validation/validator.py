"""Glavni SQL Validator — pipeline koji povezuje sve provjere.

Pipeline (po koracima):

    raw SQL string
        │
        ├─▶ 1. AST parsing (ast_checks)       → ValidationError ako fail
        │
        ├─▶ 2. Safety checks (safety_checks)  → blocked_reason ako fail
        │
        ├─▶ 3. Semantic checks (semantic)     → errors ako fail
        │
        ├─▶ 4. Enforcers (auto-LIMIT, normalize)
        │
        └─▶ ValidationResult

**Razlika između ``blocked_reason`` i ``errors``** (ključno za RetryEngine):

- ``blocked_reason`` (safety fail) → NIKAD se ne retry-a. To je signal
  potencijalnog napada (DROP, DELETE, multi-statement). Vraćamo korisniku.

- ``errors`` (parse / semantic fail) → retry je opravdan. LLM je vjerojatno
  halucinirao kolonu ili napravio syntax slip; popravljivo je.

Ovo razdvajanje je dizajnirano da RetryEngine ne troši budžet pokušaja na
napade i da napadi ne dobiju "drugu šansu".
"""

from __future__ import annotations

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.db.schema_inspector import DatabaseSchema, SchemaInspector
from app.models.validation import ValidationResult
from app.validation.ast_checks import parse_sql
from app.validation.enforcers import ensure_limit, normalize_sql
from app.validation.safety_checks import check_safety
from app.validation.semantic_checks import check_semantics

logger = get_logger(__name__)


class SqlValidator:
    """Pipeline za validaciju LLM-generiranog SQL-a.

    Dialect-aware: postoji default dialect (postavljen u konstruktoru), ali
    se može override-ati po pozivu (``validate(sql, dialect="sqlite")``).
    Razlog: Chinook demo ide PostgreSQL-om, BIRD benchmark ide SQLite-om —
    isti validator-klasa, drugačiji dialect prema kontekstu.
    """

    def __init__(
        self,
        schema_inspector: SchemaInspector,
        default_limit: int,
        default_dialect: str = "postgres",
    ) -> None:
        self._inspector = schema_inspector
        self._default_limit = default_limit
        self._default_dialect = default_dialect

    async def validate(
        self,
        sql: str,
        dialect: str | None = None,
        schema_override: DatabaseSchema | None = None,
        enforce_limit: bool = True,
    ) -> ValidationResult:
        """Vrati ``ValidationResult`` za zadani SQL.

        Args:
            sql: očišćen SQL string (već prošao ``extract_sql``).
            dialect: opcionalan override dialect-a (``"postgres"`` ili
                ``"sqlite"``). ``None`` → koristi ``self._default_dialect``.
            schema_override: opcionalan eksplicitan schema umjesto onog iz
                konstruktorovog SchemaInspector-a. Koristi se za benchmark
                gdje shema dolazi iz druge baze (per BIRD db_id).
            enforce_limit: ako je ``True`` (default), validator automatski
                dodaje ``LIMIT N`` na top-level SELECT ako ne postoji.
                Postavi ``False`` u benchmark mode-u — auto-LIMIT
                artificially trunca korektne queries koje vraćaju >N redaka
                i lažno marka ih kao "wrong_result" u EX metrici.

        Returns:
            ValidationResult s točno jednim od stanja:
            - ``ok=True, normalized_sql=...``  → spreman za izvršavanje
            - ``ok=False, blocked_reason=...`` → sigurnosni blok (NE retry-aj)
            - ``ok=False, errors=[...]``      → ispravljive greške (retry OK)
        """

        effective_dialect = dialect or self._default_dialect

        # Korak 1: AST parsing + multi-statement check.
        try:
            ast = parse_sql(sql, dialect=effective_dialect)
        except ValidationError as exc:
            logger.warning("validation.parse.fail", reason=str(exc))
            if "multi-statement" in str(exc).lower():
                return ValidationResult(ok=False, blocked_reason=str(exc))
            return ValidationResult(ok=False, errors=[str(exc)])

        # Korak 2: Safety checks. Bilo kakav fail → blocked_reason.
        safety_issues = check_safety(ast)
        if safety_issues:
            reason = " | ".join(safety_issues)
            logger.warning("validation.safety.blocked", reason=reason, sql=sql)
            return ValidationResult(ok=False, blocked_reason=reason)

        # Korak 3: Semantic checks. Fail → errors (retry kandidat).
        schema = schema_override if schema_override is not None else await self._inspector.get_schema()
        semantic_issues = check_semantics(ast, schema)
        if semantic_issues:
            logger.info("validation.semantic.fail", issues=semantic_issues)
            return ValidationResult(ok=False, errors=semantic_issues)

        # Korak 4: Enforcers — auto-LIMIT (opcionalno) i normalizacija.
        # U benchmark mode-u (enforce_limit=False) ne diramo LIMIT da bi
        # rezultat predicted-vs-gold usporedbe bio fer (gold se ne LIMIT-ira).
        if enforce_limit:
            enforced_ast = ensure_limit(ast, self._default_limit)
        else:
            enforced_ast = ast
        normalized = normalize_sql(enforced_ast, dialect=effective_dialect)

        logger.info("validation.ok", normalized=normalized[:200], dialect=effective_dialect)
        return ValidationResult(ok=True, normalized_sql=normalized)
