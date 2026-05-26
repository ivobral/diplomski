"""Komparativna analiza N benchmark run-ova.

Pokazuje **delta-evolution** EX-a, near-miss-a, wrong_result sub-tipova
kroz vrijeme — npr. main #1 → #2 → #3 → #4 → #5 nakon različitih
mjera poboljšanja.

Idealan input za thesis tablicu "Pregled poboljšanja":

| Run | Improvement       | D EX  | Δ vs prev | wrong_result | near_miss |
|-----|-------------------|-------|-----------|--------------|-----------|
| #1  | baseline          | 12%   | -         | 30           | -         |
| #2  | + evidence/medium | 28%   | +16 pp    | 30           | -         |
| #3  | + gpt-5.1         | 28%   | 0         | 30           | -         |
| #5  | + 4 improvements  | 40%   | +12 pp    | 24           | 7         |

Pokretanje:

    docker compose exec backend python /app/scripts/compare_runs.py \\
        /app/data/benchmark_runs/run1.json \\
        /app/data/benchmark_runs/run2.json \\
        /app/data/benchmark_runs/run3.json \\
        --labels "baseline" "+ evidence" "+ all improvements"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import settings
from app.evaluation.failure_analyzer import (
    AnalysisReport,
    analyze_run,
    load_run,
)
from app.services.benchmark_executor import BenchmarkExecutor


async def main() -> int:
    parser = argparse.ArgumentParser(description="Komparativna failure analiza")
    parser.add_argument("run_jsons", nargs="+", type=Path, help="JSON file-ovi run-ova")
    parser.add_argument(
        "--labels", nargs="+", default=None,
        help="Imena za svaki run (npr. 'baseline' 'evidence' 'all'). "
             "Mora biti isti broj kao run_jsons; inače koristi file imena."
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Markdown output file (default: stdout)"
    )
    args = parser.parse_args()

    if args.labels and len(args.labels) != len(args.run_jsons):
        print(f"ERROR: {len(args.labels)} labels but {len(args.run_jsons)} run files")
        return 1

    labels = args.labels or [p.stem for p in args.run_jsons]

    # Učitaj sve runove + re-execute gold (jednom)
    print(f"Loading {len(args.run_jsons)} run-ova…")
    runs_data: list[dict] = []
    all_questions: set[tuple[int, str, str]] = set()
    for path in args.run_jsons:
        if not path.exists():
            print(f"File nije pronađen: {path}")
            return 1
        data = load_run(path)
        runs_data.append(data)
        for r in data["question_results"]:
            all_questions.add((r["question_id"], r["db_id"], r["gold_sql"]))

    print(f"Re-executing {len(all_questions)} unique gold SQL queries…")
    executor = BenchmarkExecutor(
        dataset_path=Path("/app/data/bird_mini"),
        timeout_seconds=settings.QUERY_TIMEOUT_SECONDS,
    )
    gold_results: dict[int, list[list] | None] = {}
    for q_id, db_id, gold_sql in sorted(all_questions):
        if q_id in gold_results:
            continue
        try:
            ex = await executor.execute(gold_sql, db_id=db_id)
            gold_results[q_id] = ex.rows
        except Exception:
            gold_results[q_id] = None

    # Analiziraj svaki
    print("Analyzing…")
    reports: list[AnalysisReport] = []
    for data in runs_data:
        reports.append(analyze_run(data, gold_results))

    md = render_comparison(reports, labels)

    if args.output:
        args.output.write_text(md, encoding="utf-8")
        print(f"\nMarkdown report saved: {args.output}")

    print()
    print(md)
    return 0


def render_comparison(reports: list[AnalysisReport], labels: list[str]) -> str:
    """Generira markdown s delta-tablicama između runova."""

    lines: list[str] = []
    lines.append("# Run Comparison Report")
    lines.append("")
    lines.append(f"Comparing {len(reports)} runs:")
    for label, rep in zip(labels, reports, strict=False):
        lines.append(f"- **{label}** — `{rep.run_id}`")
    lines.append("")

    # ----- D strategija EX progresija -----
    lines.append("## D strategy — EX progression")
    lines.append("")
    lines.append("| # | Label | D total | OK | EX (strict) | EX (lenient) | near_miss | Δ EX |")
    lines.append("|---|-------|--------:|---:|------------:|-------------:|----------:|------|")
    prev_ex: float | None = None
    for i, (label, rep) in enumerate(zip(labels, reports, strict=False), start=1):
        d = rep.by_strategy.get("D")
        if d is None:
            lines.append(f"| {i} | {label} | — | — | — | — | — | — |")
            continue
        ex = d.execution_accuracy
        delta = f"{(ex - prev_ex)*100:+.1f} pp" if prev_ex is not None else "—"
        lines.append(
            f"| {i} | {label} | {d.total} | {d.ok_count} "
            f"| {ex*100:.1f}% | {d.lenient_accuracy*100:.1f}% "
            f"| {d.near_miss_count} | {delta} |"
        )
        prev_ex = ex
    lines.append("")

    # ----- Per strategy comparison -----
    lines.append("## All strategies — EX comparison")
    lines.append("")
    header = "| Run | " + " | ".join(f"{c}" for c in ["A", "B", "C", "D"]) + " |"
    lines.append(header)
    lines.append("|" + "-----|" * 5)
    for label, rep in zip(labels, reports, strict=False):
        row = f"| {label} |"
        for code in ["A", "B", "C", "D"]:
            agg = rep.by_strategy.get(code)
            if agg is None:
                row += " — |"
            else:
                row += f" {agg.execution_accuracy*100:.1f}% |"
        lines.append(row)
    lines.append("")

    # ----- Wrong result subtype evolution -----
    lines.append("## Wrong-result root cause evolution (D strategija)")
    lines.append("")
    all_subs: set[str] = set()
    for rep in reports:
        all_subs.update(rep.wrong_result_subtypes_d.keys())

    if all_subs:
        sorted_subs = sorted(all_subs)
        header = "| Sub-cause | " + " | ".join(labels) + " |"
        lines.append(header)
        lines.append("|" + "----|" * (len(labels) + 1))
        for sub in sorted_subs:
            row = f"| `{sub}` |"
            for rep in reports:
                row += f" {rep.wrong_result_subtypes_d.get(sub, 0)} |"
            lines.append(row)
        lines.append("")
    else:
        lines.append("_Nema wrong_result rezultata u analiziranim run-ovima._")
        lines.append("")

    # ----- Difficulty progression for D -----
    lines.append("## D EX po difficulty (po runovima)")
    lines.append("")
    header = "| Difficulty | " + " | ".join(labels) + " |"
    lines.append(header)
    lines.append("|" + "----|" * (len(labels) + 1))
    for diff in ["simple", "moderate", "challenging"]:
        row = f"| {diff} |"
        for rep in reports:
            agg = rep.by_difficulty_d_only.get(diff)
            if agg is None or agg.total == 0:
                row += " — |"
            else:
                row += f" {agg.execution_accuracy*100:.1f}% ({agg.ok_count}/{agg.total}) |"
        lines.append(row)
    lines.append("")

    # ----- Per-database progression -----
    lines.append("## D EX po BIRD bazi (po runovima)")
    lines.append("")
    all_dbs: set[str] = set()
    for rep in reports:
        all_dbs.update(rep.by_database_d_only.keys())
    header = "| Database | " + " | ".join(labels) + " |"
    lines.append(header)
    lines.append("|" + "----|" * (len(labels) + 1))
    for db in sorted(all_dbs):
        row = f"| {db} |"
        for rep in reports:
            agg = rep.by_database_d_only.get(db)
            if agg is None or agg.total == 0:
                row += " — |"
            else:
                row += f" {agg.execution_accuracy*100:.1f}% ({agg.ok_count}/{agg.total}) |"
        lines.append(row)
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
