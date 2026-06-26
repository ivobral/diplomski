"""Robusna failure analiza za benchmark run-ove.

Cilj: za svaki rezultat odrediti **root cause** neuspjeha kroz strukturalnu
usporedbu predicted vs gold SQL-a (sqlglot AST diff) + multi-dimenzijska
agregacija (strategija × težina × baza × vrsta neuspjeha).

Akademski narrative iz outputa:
- "X% neuspjeha je `wrong_join` — sustav teško razumije kompleksne JOIN-ove"
- "Schema linking dodavanjem smanjio `wrong_join` s 12 na 4"
- "Pri evidence-aware promptingu, `wrong_filter` pada s 8 na 3"

Modul je razdvojen od CLI skripte da se može koristiti i programatski
(npr. iz pytest testova, ili iz buduće UI sekcije).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import expressions as exp

from app.evaluation.comparators import rows_equal

# ----------------------------------------------------------------------
# Failure kategorije — hijerarhijska struktura.
# ----------------------------------------------------------------------

# Top-level kategorije (najopćenitije, prvi sloj klasifikacije).
PRIMARY_CATEGORIES = [
    "ok",                       # correct
    "near_miss_column_order",   # data right, structure off
    "wrong_result",             # data wrong (treba sub-classification)
    "blocked_by_safety",
    "llm_refused",
    "empty_sql",
    "hallucinated_table",
    "hallucinated_column",
    "bad_alias",
    "execution_error",
    "exception",
    "no_gold",
]

# Sub-kategorije za "wrong_result" — root cause iz SQL strukturalnog diff-a.
WRONG_RESULT_SUBTYPES = [
    "wrong_tables",       # different FROM/JOIN tables
    "wrong_filter",       # different WHERE columns/operators
    "wrong_aggregate",    # different aggregate functions or columns
    "missing_group_by",   # gold has GROUP BY, predicted doesn't
    "wrong_group_by",     # different GROUP BY columns
    "wrong_columns",      # different SELECT columns
    "wrong_sort",         # different ORDER BY
    "missing_distinct",   # gold has DISTINCT, predicted doesn't
    "subquery_mismatch",  # gold uses subquery, predicted joins (or vice versa)
    "multiple_issues",    # 2+ above
    "unknown",            # SQL parsing failed or structure is too different
]


# ----------------------------------------------------------------------
# Dataclasses za rezultat analize.
# ----------------------------------------------------------------------


@dataclass(slots=True)
class FailureRecord:
    """Klasificiran rezultat za jedno (question, strategy) pitanje."""

    question_id: int
    db_id: str
    question: str
    difficulty: str
    strategy: str
    provider: str
    primary_category: str
    sub_category: str | None  # samo za wrong_result
    structural_diffs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AggregateStats:
    """Agregirane brojke za neku grupu (npr. po strategiji)."""

    label: str
    total: int = 0
    by_category: Counter = field(default_factory=Counter)

    @property
    def ok_count(self) -> int:
        return self.by_category.get("ok", 0)

    @property
    def near_miss_count(self) -> int:
        return self.by_category.get("near_miss_column_order", 0)

    @property
    def execution_accuracy(self) -> float:
        """Strict BIRD EX — samo `ok` se broji."""

        return self.ok_count / self.total if self.total else 0.0

    @property
    def lenient_accuracy(self) -> float:
        """Permissive EX — `ok` + `near_miss_column_order` (data correct, structure varies)."""

        return (self.ok_count + self.near_miss_count) / self.total if self.total else 0.0


# ----------------------------------------------------------------------
# SQL strukturalni diff
# ----------------------------------------------------------------------


def extract_sql_features(sql: str) -> dict[str, Any] | None:
    """Izvuci strukturalne značajke iz SQL-a kroz sqlglot AST.

    Returns:
        Dict s features-ima ili ``None`` ako parsing pada. Features:
        - ``tables``: set imena tablica (bez aliasa)
        - ``select_columns``: lista (table_or_alias, column) parova
        - ``where_columns``: lista kolona u WHERE clause
        - ``aggregates``: lista (func_name, column_name) parova
        - ``group_by_columns``: lista kolona u GROUP BY
        - ``order_by_columns``: lista kolona u ORDER BY
        - ``has_distinct``: bool
        - ``has_subquery``: bool
    """

    try:
        ast = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return None

    if ast is None:
        return None

    # Skupljamo strukture obilaskom AST-a. Sve identifikatore lowercase-amo
    # za case-insensitive usporedbu (BIRD ima neke baze s mixed-case kolonama).
    tables = {t.name.lower() for t in ast.find_all(exp.Table)}

    # WHERE — pronađi sve eksterne Column reference unutar Where nodes.
    where_columns: list[str] = []
    for where_node in ast.find_all(exp.Where):
        for col in where_node.find_all(exp.Column):
            where_columns.append(col.name.lower())

    # SELECT projection columns (samo prvi-razinski SELECT, ne subqueries).
    select_columns: list[str] = []
    # ast.this je SELECT node za WITH, inače je ast sam SELECT
    top_select = ast.this if isinstance(ast, exp.With) else ast
    if isinstance(top_select, exp.Select):
        for proj in top_select.expressions:
            # Spusti Alias da dobiješ pravi expr
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(inner, exp.Column):
                select_columns.append(inner.name.lower())
            else:
                # Aggregate, function, expression — ime mu je sam tip
                select_columns.append(type(inner).__name__.lower())

    # Aggregate functions
    aggregates: list[str] = []
    for agg_class in (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max):
        for agg in ast.find_all(agg_class):
            inner_col = agg.this
            col_name = (
                inner_col.name.lower() if isinstance(inner_col, exp.Column) else "expr"
            )
            aggregates.append(f"{agg_class.__name__.lower()}({col_name})")

    # GROUP BY
    group_by_columns: list[str] = []
    for gb in ast.find_all(exp.Group):
        for col in gb.find_all(exp.Column):
            group_by_columns.append(col.name.lower())

    # ORDER BY
    order_by_columns: list[str] = []
    for ob in ast.find_all(exp.Order):
        for col in ob.find_all(exp.Column):
            order_by_columns.append(col.name.lower())

    # DISTINCT, subquery presence
    has_distinct = isinstance(top_select, exp.Select) and bool(top_select.args.get("distinct"))
    # Subquery = bilo koji Select inside drugi Select
    has_subquery = False
    if isinstance(top_select, exp.Select):
        # Provjeri jesu li ikakvi nested SELECT-ovi unutar (sami AST node).
        nested = [s for s in top_select.find_all(exp.Select) if s is not top_select]
        has_subquery = len(nested) > 0

    return {
        "tables": tables,
        "select_columns": sorted(select_columns),
        "where_columns": sorted(set(where_columns)),
        "aggregates": sorted(set(aggregates)),
        "group_by_columns": sorted(set(group_by_columns)),
        "order_by_columns": sorted(set(order_by_columns)),
        "has_distinct": has_distinct,
        "has_subquery": has_subquery,
    }


def classify_wrong_result(predicted_sql: str | None, gold_sql: str) -> tuple[str, list[str]]:
    """Klasificira `wrong_result` u sub-tip + vraća listu detaljnih diff-ova.

    Returns:
        ``(sub_category, diff_list)``. Diff list je human-readable raspis
        za markdown report.
    """

    if not predicted_sql:
        return "unknown", ["predicted SQL is empty"]

    pred = extract_sql_features(predicted_sql)
    gold = extract_sql_features(gold_sql)
    if pred is None or gold is None:
        return "unknown", ["parsing failed for predicted or gold SQL"]

    diffs: list[str] = []

    # Provjeri razlike, počevši od najvažnijih.
    if pred["tables"] != gold["tables"]:
        diffs.append(
            f"tables: pred={sorted(pred['tables'])} vs gold={sorted(gold['tables'])}"
        )

    pred_where = set(pred["where_columns"])
    gold_where = set(gold["where_columns"])
    if pred_where != gold_where:
        missing = gold_where - pred_where
        extra = pred_where - gold_where
        msg_parts = []
        if missing:
            msg_parts.append(f"missing filter cols: {sorted(missing)}")
        if extra:
            msg_parts.append(f"extra filter cols: {sorted(extra)}")
        diffs.append("where: " + "; ".join(msg_parts))

    if pred["aggregates"] != gold["aggregates"]:
        diffs.append(f"aggregates: pred={pred['aggregates']} vs gold={gold['aggregates']}")

    gold_has_gb = bool(gold["group_by_columns"])
    pred_has_gb = bool(pred["group_by_columns"])
    if gold_has_gb and not pred_has_gb:
        diffs.append(f"missing GROUP BY: gold={gold['group_by_columns']}")
    elif pred["group_by_columns"] != gold["group_by_columns"]:
        diffs.append(
            f"group by: pred={pred['group_by_columns']} vs gold={gold['group_by_columns']}"
        )

    if pred["select_columns"] != gold["select_columns"]:
        diffs.append(
            f"select cols: pred={pred['select_columns']} vs gold={gold['select_columns']}"
        )

    if pred["order_by_columns"] != gold["order_by_columns"]:
        diffs.append(
            f"order by: pred={pred['order_by_columns']} vs gold={gold['order_by_columns']}"
        )

    if pred["has_distinct"] != gold["has_distinct"]:
        diffs.append(
            f"distinct: pred={pred['has_distinct']} vs gold={gold['has_distinct']}"
        )

    if pred["has_subquery"] != gold["has_subquery"]:
        diffs.append(
            f"subquery shape: pred={pred['has_subquery']} vs gold={gold['has_subquery']}"
        )

    # Sub-kategorija = prvi (najvažniji) diff. Ako 2+, multiple_issues.
    if not diffs:
        return "unknown", ["no structural differences detected (semantic value mismatch?)"]

    # Klasificiraj po prvom diff-u, ali bilježimo broj za multiple_issues
    primary_diff = diffs[0]
    sub_category = _diff_to_subcategory(primary_diff)

    if len(diffs) >= 3:
        return "multiple_issues", diffs

    return sub_category, diffs


def _diff_to_subcategory(diff_msg: str) -> str:
    """Mapira diff string u sub-kategoriju."""

    if diff_msg.startswith("tables:"):
        return "wrong_tables"
    if diff_msg.startswith("where:"):
        return "wrong_filter"
    if diff_msg.startswith("aggregates:"):
        return "wrong_aggregate"
    if diff_msg.startswith("missing GROUP BY"):
        return "missing_group_by"
    if diff_msg.startswith("group by:"):
        return "wrong_group_by"
    if diff_msg.startswith("select cols:"):
        return "wrong_columns"
    if diff_msg.startswith("order by:"):
        return "wrong_sort"
    if diff_msg.startswith("distinct:"):
        return "missing_distinct"
    if diff_msg.startswith("subquery"):
        return "subquery_mismatch"
    return "unknown"


# ----------------------------------------------------------------------
# Permissive comparator (near_miss_column_order)
# ----------------------------------------------------------------------


def predicted_contains_gold(predicted: list[list], gold: list[list]) -> bool:
    """True ako pred rows multiset-of-values uključuje sve gold rows.

    Permissive: ignorira column order i extra columns. Koristi se za
    near_miss detekciju.
    """

    if len(predicted) != len(gold):
        return False

    # Stringify cells za type-safe usporedbu (None, int, Decimal mix).
    def _row_to_multiset(row: list) -> Counter:
        return Counter("None" if v is None else str(v) for v in row)

    pred_pool = [_row_to_multiset(r) for r in predicted]
    gold_pool = [_row_to_multiset(r) for r in gold]

    # Greedy match: za svaki gold red, nađi pred red koji ga sadrži kao subset
    used = [False] * len(pred_pool)
    for gold_row in gold_pool:
        matched = False
        for i, pred_row in enumerate(pred_pool):
            if used[i]:
                continue
            if all(pred_row[c] >= gold_row[c] for c in gold_row):
                used[i] = True
                matched = True
                break
        if not matched:
            return False
    return True


# ----------------------------------------------------------------------
# Glavni analyzer
# ----------------------------------------------------------------------


def categorize_result(result: dict, gold_rows: list[list] | None) -> tuple[str, str | None, list[str]]:
    """Vrati (primary_category, sub_category, diff_list) za jedan rezultat."""

    err = (result.get("error_reason") or "").lower()

    if result.get("blocked"):
        return "blocked_by_safety", None, []

    if err.startswith("exception"):
        return "exception", None, []

    predicted_sql = result.get("predicted_sql") or ""
    if not predicted_sql:
        return "empty_sql", None, []

    if "unable to answer" in predicted_sql.lower():
        return "llm_refused", None, []

    if not result.get("validated"):
        if "prazan sql" in err or "parsing" in err:
            return "empty_sql", None, []
        if "tablica" in err and "ne postoji" in err:
            return "hallucinated_table", None, []
        if "kolona" in err and "ne postoji" in err:
            return "hallucinated_column", None, []
        if "kvalifikator" in err:
            return "bad_alias", None, []
        return "execution_error", None, []

    if not result.get("executed"):
        return "execution_error", None, []

    if gold_rows is None:
        return "no_gold", None, []

    predicted_rows = result.get("predicted_rows") or []
    if rows_equal(predicted_rows, gold_rows, strict_order=False):
        return "ok", None, []

    # Near miss (column order/extra columns ignored)
    if predicted_contains_gold(predicted_rows, gold_rows):
        return "near_miss_column_order", None, []

    # Wrong result — klasificiraj sub-tip kroz SQL diff
    sub_cat, diffs = classify_wrong_result(predicted_sql, result.get("gold_sql", ""))
    return "wrong_result", sub_cat, diffs


@dataclass(slots=True)
class AnalysisReport:
    """Cjeloviti report jednog run-a."""

    run_id: str
    config: dict[str, Any]
    records: list[FailureRecord]
    overall: AggregateStats
    by_strategy: dict[str, AggregateStats]
    by_difficulty_d_only: dict[str, AggregateStats]
    by_database_d_only: dict[str, AggregateStats]
    wrong_result_subtypes_d: Counter
    # New: wrong-result subtypes pivoted by difficulty (D strategy only).
    # Shape: {"simple": Counter, "moderate": Counter, "challenging": Counter}
    wrong_subtypes_by_difficulty_d: dict[str, Counter] = field(default_factory=dict)


def analyze_run(
    run_data: dict, gold_results: dict[int, list[list] | None]
) -> AnalysisReport:
    """Analiziraj jedan benchmark run + vrati strukturirani report.

    Args:
        run_data: učitani JSON s `question_results` + `config`.
        gold_results: mapping question_id → izvršeni gold rezultati.

    Returns:
        ``AnalysisReport`` s svim agregatima i records-ima.
    """

    records: list[FailureRecord] = []
    overall = AggregateStats(label="overall")
    by_strategy: dict[str, AggregateStats] = defaultdict(
        lambda: AggregateStats(label="")
    )
    by_difficulty_d: dict[str, AggregateStats] = defaultdict(
        lambda: AggregateStats(label="")
    )
    by_database_d: dict[str, AggregateStats] = defaultdict(
        lambda: AggregateStats(label="")
    )
    wrong_subtypes_d: Counter = Counter()
    # New: per-difficulty wrong-result subtypes (D strategy only).
    wrong_subtypes_by_diff: dict[str, Counter] = defaultdict(Counter)

    for r in run_data["question_results"]:
        gold = gold_results.get(r["question_id"])
        primary, sub, diffs = categorize_result(r, gold)

        record = FailureRecord(
            question_id=r["question_id"],
            db_id=r["db_id"],
            question=r["question"],
            difficulty=r["difficulty"],
            strategy=r["strategy"],
            provider=r["provider"],
            primary_category=primary,
            sub_category=sub,
            structural_diffs=diffs,
        )
        records.append(record)

        overall.total += 1
        overall.by_category[primary] += 1

        strat_agg = by_strategy[r["strategy"]]
        strat_agg.label = r["strategy"]
        strat_agg.total += 1
        strat_agg.by_category[primary] += 1

        if r["strategy"] == "D":
            diff_agg = by_difficulty_d[r["difficulty"]]
            diff_agg.label = r["difficulty"]
            diff_agg.total += 1
            diff_agg.by_category[primary] += 1

            db_agg = by_database_d[r["db_id"]]
            db_agg.label = r["db_id"]
            db_agg.total += 1
            db_agg.by_category[primary] += 1

            if primary == "wrong_result" and sub:
                wrong_subtypes_d[sub] += 1
                wrong_subtypes_by_diff[r["difficulty"]][sub] += 1

    return AnalysisReport(
        run_id=run_data.get("run_id", "?"),
        config=run_data.get("config", {}),
        records=records,
        overall=overall,
        by_strategy=dict(by_strategy),
        by_difficulty_d_only=dict(by_difficulty_d),
        by_database_d_only=dict(by_database_d),
        wrong_result_subtypes_d=wrong_subtypes_d,
        wrong_subtypes_by_difficulty_d=dict(wrong_subtypes_by_diff),
    )


# ----------------------------------------------------------------------
# Markdown report generator
# ----------------------------------------------------------------------


def render_markdown(report: AnalysisReport, show_examples: int = 2) -> str:
    """Generira markdown report spreman za uvrstavanje u rad.

    Args:
        report: rezultat ``analyze_run``.
        show_examples: koliko primjera po wrong_result sub-tipu prikazati.
    """

    lines: list[str] = []
    lines.append(f"# Benchmark Failure Report — `{report.run_id}`")
    lines.append("")

    # ----- Config + headline -----
    lines.append("## Config")
    cfg = report.config
    lines.append(f"- Providers: `{cfg.get('providers')}`")
    lines.append(f"- Strategies: `{cfg.get('strategies')}`")
    lines.append(f"- Limit: {cfg.get('limit')}")
    lines.append(f"- Difficulty filter: {cfg.get('difficulty') or 'all'}")
    lines.append("")

    # ----- Headline metrics -----
    lines.append("## Headline metrics")
    lines.append(f"- Total questions evaluated: **{report.overall.total}**")
    lines.append("")
    lines.append("| Strategy | Total | EX (strict) | EX (lenient) | near_miss |")
    lines.append("|----------|------:|-----------:|-------------:|----------:|")
    for code in ["A", "B", "C", "D"]:
        if code in report.by_strategy:
            agg = report.by_strategy[code]
            lines.append(
                f"| {code} | {agg.total} | {agg.execution_accuracy*100:.1f}% "
                f"| {agg.lenient_accuracy*100:.1f}% | {agg.near_miss_count} |"
            )
    lines.append("")
    lines.append(
        "_EX strict_ = BIRD-compatibilni (column order matters). "
        "_EX lenient_ = data correct ignoring column order/extras."
    )
    lines.append("")

    # ----- D strategy detail -----
    lines.append("## Strategy D detalji (najreprezentativnija)")
    if "D" in report.by_strategy:
        d = report.by_strategy["D"]
        lines.append("")
        lines.append("### By difficulty")
        lines.append("")
        lines.append("| Difficulty | Total | OK | Near miss | EX |")
        lines.append("|------------|------:|---:|----------:|---:|")
        for diff in ["simple", "moderate", "challenging"]:
            if diff in report.by_difficulty_d_only:
                d_agg = report.by_difficulty_d_only[diff]
                lines.append(
                    f"| {diff} | {d_agg.total} | {d_agg.ok_count} "
                    f"| {d_agg.near_miss_count} | {d_agg.execution_accuracy*100:.1f}% |"
                )
        lines.append("")

        lines.append("### By database")
        lines.append("")
        lines.append("| Database | Total | OK | EX |")
        lines.append("|----------|------:|---:|---:|")
        for db, db_agg in sorted(report.by_database_d_only.items()):
            lines.append(
                f"| {db} | {db_agg.total} | {db_agg.ok_count} | {db_agg.execution_accuracy*100:.1f}% |"
            )
        lines.append("")

        lines.append("### Wrong-result root causes (D strategija)")
        lines.append("")
        lines.append("| Sub-kategorija | Count |")
        lines.append("|----------------|------:|")
        for sub in WRONG_RESULT_SUBTYPES:
            if report.wrong_result_subtypes_d[sub]:
                lines.append(f"| {sub} | {report.wrong_result_subtypes_d[sub]} |")
        lines.append("")

        # ----- Per-difficulty pivot -----
        # Pivots wrong-result subtypes by difficulty so the discussion can
        # claim things like "wrong_columns dominates on challenging, not
        # on simple". Only shows subtypes that appear at least once.
        if report.wrong_subtypes_by_difficulty_d:
            lines.append("### Wrong-result by difficulty (D strategija)")
            lines.append("")
            difficulties_present = [
                d for d in ("simple", "moderate", "challenging")
                if d in report.wrong_subtypes_by_difficulty_d
            ]

            header = ["Sub-kategorija"] + [
                f"{d} ({report.by_difficulty_d_only[d].total if d in report.by_difficulty_d_only else 0})"
                for d in difficulties_present
            ]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "|".join(["---"] + ["---:"] * len(difficulties_present)) + "|")

            for sub in WRONG_RESULT_SUBTYPES:
                # Skip subtypes with no occurrences across any difficulty.
                if not any(
                    report.wrong_subtypes_by_difficulty_d.get(d, Counter())[sub]
                    for d in difficulties_present
                ):
                    continue
                row = [sub]
                for d in difficulties_present:
                    count = report.wrong_subtypes_by_difficulty_d.get(d, Counter())[sub]
                    total_in_diff = (
                        report.by_difficulty_d_only[d].total
                        if d in report.by_difficulty_d_only
                        else 0
                    )
                    pct = f" ({count / total_in_diff * 100:.0f}%)" if total_in_diff else ""
                    row.append(f"{count}{pct}")
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

    # ----- Concrete examples per sub-category (D only) -----
    lines.append("## Konkretni primjeri (D strategija)")
    lines.append("")
    shown_per_sub: dict[str, int] = defaultdict(int)
    for record in report.records:
        if record.strategy != "D":
            continue
        if record.primary_category != "wrong_result":
            continue
        if not record.sub_category:
            continue
        if shown_per_sub[record.sub_category] >= show_examples:
            continue
        shown_per_sub[record.sub_category] += 1

        lines.append(f"### `{record.sub_category}` — q_id={record.question_id} ({record.difficulty})")
        lines.append(f"DB: `{record.db_id}`")
        lines.append(f"**Question**: {record.question}")
        lines.append("")
        lines.append("**Diff:**")
        for d in record.structural_diffs[:5]:
            lines.append(f"- {d}")
        lines.append("")

    # ----- Insights -----
    lines.append("## Insights")
    if "D" in report.by_strategy:
        d = report.by_strategy["D"]
        wrong = d.by_category.get("wrong_result", 0)
        lines.append(f"- Glavni problem na D: **{wrong}/{d.total} wrong_result**")
        if report.wrong_result_subtypes_d:
            top_sub, top_count = report.wrong_result_subtypes_d.most_common(1)[0]
            lines.append(f"- Najčešći wrong_result tip: **{top_sub}** ({top_count})")
        delta_lenient = (d.lenient_accuracy - d.execution_accuracy) * 100
        lines.append(
            f"- Razlika strict vs lenient EX: **+{delta_lenient:.1f} pp** "
            f"(kolone/extra cols issue)"
        )

    return "\n".join(lines)


# ----------------------------------------------------------------------
# JSON loader helper
# ----------------------------------------------------------------------


def load_run(path: Path) -> dict:
    """Učitaj benchmark run JSON s diska."""

    with path.open(encoding="utf-8") as f:
        return json.load(f)
