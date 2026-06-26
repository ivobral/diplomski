"""Append-only JSONL audit trail for every query executed.

Defensive: failure to write the audit entry never breaks the request.
The trail is useful for post-hoc analysis (thesis discussion section),
debugging unexpected results, and proving the system actually answered N
queries with X% validation success.

Location: ``/app/data/audit_log.jsonl`` (inside the container) =
``./data/audit_log.jsonl`` on the host (mounted volume).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_AUDIT_FILE = Path("/app/data/audit_log.jsonl")


def write_audit_entry(entry: dict[str, Any]) -> None:
    """Append one entry to the JSONL audit log.

    `default=str` lets datetime, Pydantic models etc. serialize without
    explicit conversion. The file is JSONL (one JSON per line) so it can
    be tail-ed and processed with `jq` or pandas.read_json(lines=True).
    """

    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001 — audit must never break the request
        logger.warning("audit.write_failed", error=str(exc))


def make_query_entry(
    *,
    question: str,
    strategy: str,
    provider: str,
    generated_sql: str | None,
    normalized_sql: str | None,
    validated: bool,
    executed: bool,
    blocked_reason: str | None,
    error: str | None,
    row_count: int,
    retry_count: int,
    latency: dict[str, float | None],
) -> dict[str, Any]:
    """Build a canonical audit entry shape for /api/query."""

    return {
        "ts": datetime.now(UTC).isoformat(),
        "kind": "query",
        "question": question,
        "strategy": strategy,
        "provider": provider,
        "generated_sql": generated_sql,
        "normalized_sql": normalized_sql,
        "validated": validated,
        "executed": executed,
        "blocked_reason": blocked_reason,
        "error": error,
        "row_count": row_count,
        "retry_count": retry_count,
        "latency": latency,
    }
