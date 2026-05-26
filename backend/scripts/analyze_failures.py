"""CLI za failure analizu benchmark run-ova.

Koristi ``app.evaluation.failure_analyzer`` modul za pravu logiku;
ovaj script samo orkestrira gold execution + I/O.

Pokretanje:

    docker compose exec backend python /app/scripts/analyze_failures.py \\
        /app/data/benchmark_runs/<run_id>.json

Opcionalno, generiraj markdown report:

    docker compose exec backend python /app/scripts/analyze_failures.py \\
        /app/data/benchmark_runs/<run_id>.json \\
        --output /app/data/benchmark_runs/<run_id>.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import settings
from app.evaluation.failure_analyzer import analyze_run, load_run, render_markdown
from app.services.benchmark_executor import BenchmarkExecutor


async def main() -> int:
    parser = argparse.ArgumentParser(description="Robusna failure analiza benchmark run-a")
    parser.add_argument("run_json", type=Path, help="Path do benchmark run JSON-a")
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Path za markdown output (default: ispisuje na stdout summary)"
    )
    parser.add_argument(
        "--examples", "-e", type=int, default=2,
        help="Broj primjera po wrong_result sub-tipu (default 2)"
    )
    args = parser.parse_args()

    if not args.run_json.exists():
        print(f"File nije pronađen: {args.run_json}")
        return 1

    print(f"Loading run: {args.run_json.name}")
    run_data = load_run(args.run_json)
    results = run_data["question_results"]
    print(f"  total results: {len(results)}")

    # Re-execute gold SQL (cached po question_id)
    executor = BenchmarkExecutor(
        dataset_path=Path("/app/data/bird_mini"),
        timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
    )
    unique_questions = {(r["question_id"], r["db_id"], r["gold_sql"]) for r in results}
    print(f"Re-executing {len(unique_questions)} unique gold SQL queries…")

    gold_results: dict[int, list[list] | None] = {}
    for q_id, db_id, gold_sql in sorted(unique_questions):
        if q_id in gold_results:
            continue
        try:
            ex = await executor.execute(gold_sql, db_id=db_id)
            gold_results[q_id] = ex.rows
        except Exception:
            gold_results[q_id] = None
    failed_gold = sum(1 for v in gold_results.values() if v is None)
    print(f"  gold executed OK: {len(gold_results) - failed_gold}, errors: {failed_gold}")

    # Analiziraj
    print("Analyzing…")
    report = analyze_run(run_data, gold_results)

    # Render markdown
    md = render_markdown(report, show_examples=args.examples)

    if args.output:
        args.output.write_text(md, encoding="utf-8")
        print(f"\nMarkdown report saved: {args.output}")

    # Ispisuje na stdout
    print()
    print(md)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
