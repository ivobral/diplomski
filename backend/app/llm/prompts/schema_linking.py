"""Schema linking pre-step za D strategiju.

Inspirirano DAIL-SQL paperom (Gao et al., 2023): prije generiranja SQL-a
prvo zatraži LLM-a da identificira **relevantne tablice** za pitanje.
Zatim glavni SQL-generation LLM dobiva **fokusiranu shemu** umjesto
cijele baze.

Razlog: kad baza ima 11+ tablica, model troši "pažnju" na irrelevantne
tablice i češće pogriješi joinove ili filtere. Fokusiranjem schema-e na
relevantne tablice, model bolje razumije strukturu i točnije generira SQL.

Literatura: DAIL-SQL prijavljuje +15-20 pp EX boost s schema linking-om
na full BIRD. Naš očekivani boost: +5-10 pp (jer već imamo evidence i
sample rows).

Implementacija: jednokratan LLM poziv s posebnim system promptom koji
traži samo listu imena tablica. Parser je defenzivan na različite formate
odgovora (CSV, JSON-like, jedan po liniji).
"""

from __future__ import annotations

import json
import re

from app.db.schema_inspector import ColumnInfo, DatabaseSchema, TableInfo
from app.llm.base import Prompt
from app.llm.prompts.templates import format_schema_for_prompt

SCHEMA_LINKING_SYSTEM = """\
You are a database schema analyst. Given a natural-language question and
a database schema, identify which TABLES are needed to answer the question.

OUTPUT FORMAT (strict):
- Return ONLY a comma-separated list of table names.
- No prose, no explanations, no markdown.
- Example output: schools, frpm

Be inclusive: if a table MIGHT be needed for joining or filtering,
include it. It's better to include an extra table than to miss one.
"""


# Column-level linker — drugi (opcionalni) prompt koji traži NE samo tablice
# nego i konkretne kolone unutar njih. Akademski referencirano kao
# "fine-grained schema linking" u DAIL-SQL paperu.
SCHEMA_LINKING_COLUMNS_SYSTEM = """\
You are a database schema analyst. Given a natural-language question and
a database schema, identify which TABLES and COLUMNS are needed.

OUTPUT FORMAT (strict):
- Return JSON object with structure: {"table_name": ["col1", "col2"], ...}
- Include ONLY tables and columns relevant to the question.
- Include columns used in: SELECT, WHERE filters, JOIN ON conditions, GROUP BY, ORDER BY.
- For JOINs, include the key columns from both sides.
- No prose, no markdown code fences, just the JSON object.

Example output:
{"schools": ["CDSCode", "School Name", "County"], "frpm": ["CDSCode", "Enrollment (K-12)"]}

Be inclusive on JOIN keys (PK + FK columns), conservative on data columns.
"""


SCHEMA_LINKING_USER_TEMPLATE = """\
Database schema:
{schema}

Question: {question}
{evidence_block}

Relevant tables (comma-separated):"""


def build_schema_linking_prompt(
    question: str,
    schema: DatabaseSchema,
    evidence: str = "",
) -> Prompt:
    """Build prompt za schema linking pre-step.

    Args:
        question: korisničko pitanje.
        schema: pun DatabaseSchema.
        evidence: opcionalan BIRD evidence (pomaže linker-u da razumije
            koje tablice su zapravo relevantne za semantičko značenje).
    """

    # Sažeti format — bez sample rows-a ni column descriptions u linkingu;
    # samo struktura. Linker odluku donosi na razini tablica, ne kolona.
    schema_text = format_schema_for_prompt(
        schema, include_relations=True, include_sample_rows=False
    )

    evidence_block = ""
    if evidence.strip():
        evidence_block = f"\nExpert hint: {evidence.strip()}"

    user_prompt = SCHEMA_LINKING_USER_TEMPLATE.format(
        schema=schema_text,
        question=question,
        evidence_block=evidence_block,
    )

    return Prompt(system=SCHEMA_LINKING_SYSTEM, user=user_prompt)


def parse_linked_tables(raw_response: str, available_tables: set[str]) -> list[str]:
    """Parsira LLM odgovor i vraća listu validnih imena tablica.

    Defenzivno: LLM može vratiti
    - "schools, frpm" (target format)
    - "[schools, frpm]" (JSON-like)
    - "schools\nfrpm" (newline-separated)
    - "Tables: schools, frpm" (prefiks)
    - "1. schools\n2. frpm" (numbered)
    - prazno → fallback na sve tablice

    Args:
        raw_response: LLM-ov odgovor (text content).
        available_tables: skup ALL postojećih tablica u shemi (lowercase).

    Returns:
        Lista imena tablica koje (a) LLM je predložio I (b) postoje u shemi.
        Ako parser ne pronađe ništa, vraća sve dostupne tablice (failsafe —
        bolje koristi punu shemu nego generirati besmislen SQL).
    """

    # Strip markdown code blocks i prefiks tipa "Tables:" / "Answer:"
    text = raw_response.strip()
    text = re.sub(r"^```\w*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = re.sub(r"^(tables|answer|relevant tables)\s*:\s*", "", text, flags=re.IGNORECASE)

    # Tokenize: split po zarezu, novom redu, ili numbered list-u
    candidates: list[str] = []
    for token in re.split(r"[,\n;]+", text):
        # Ukloni numbered prefix (1. table, 2. table)
        cleaned = re.sub(r"^\s*\d+[\.\)]\s*", "", token).strip()
        # Ukloni navodnike, backtick-ove
        cleaned = cleaned.strip("\"'`[]()<>")
        if cleaned:
            candidates.append(cleaned.lower())

    # Filtriraj samo na postojeće tablice (case-insensitive)
    available_lower = {t.lower() for t in available_tables}
    valid = [c for c in candidates if c in available_lower]

    # Failsafe: ako ništa nije matchalo, vrati sve tablice
    if not valid:
        return sorted(available_tables)

    # Dedupliciraj zadržavajući redoslijed
    seen: set[str] = set()
    result: list[str] = []
    for t in valid:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def filter_schema_to_tables(
    schema: DatabaseSchema,
    table_names: list[str],
) -> DatabaseSchema:
    """Vraća novi DatabaseSchema s samo navedenim tablicama.

    Case-insensitive matching. Foreign keys koji upućuju na izuzete tablice
    se zadržavaju u TableInfo-u (ne micaju) — LLM može vidjeti relaciju
    čak i ako referenced tablica nije u filtered schemi (ali rijetko će ju
    moći koristiti pa to nije problem).
    """

    target = {t.lower() for t in table_names}
    filtered_tables = tuple(t for t in schema.tables if t.name.lower() in target)
    return DatabaseSchema(tables=filtered_tables, fetched_at=schema.fetched_at)


# ---------------------------------------------------------------
# Column-level linking (DAIL-SQL fine-grained)
# ---------------------------------------------------------------


def build_column_linking_prompt(
    question: str,
    schema: DatabaseSchema,
    evidence: str = "",
) -> Prompt:
    """Build prompt za fine-grained (table+column) linking pre-step.

    Vraća se kasnije kao JSON {table: [cols]} koji se parsira kroz
    ``parse_linked_columns``.
    """

    schema_text = format_schema_for_prompt(
        schema, include_relations=True, include_sample_rows=False
    )

    evidence_block = ""
    if evidence.strip():
        evidence_block = f"\nExpert hint: {evidence.strip()}"

    user_prompt = (
        f"Database schema:\n{schema_text}\n\n"
        f"Question: {question}{evidence_block}\n\n"
        f"Relevant tables and columns (JSON):"
    )

    return Prompt(system=SCHEMA_LINKING_COLUMNS_SYSTEM, user=user_prompt)


def parse_linked_columns(
    raw_response: str,
    available_schema: DatabaseSchema,
) -> dict[str, list[str]]:
    """Parsira LLM odgovor (JSON ili JSON-like) u dict {table: [cols]}.

    Defenzivno na:
    - markdown code blocks (```json ... ```)
    - trailing/leading whitespace ili prose
    - case razlike u table/column imenima
    - missing tables/columns u outputu (vrati prazan dict; failsafe je puna schema)

    Args:
        raw_response: LLM-ov sirovi text content.
        available_schema: pun schema za case-insensitive validation.

    Returns:
        Dict {table_name_original_case: [col1, col2, ...]} samo s validnim
        tablicama/kolonama. Prazan dict znači "fallback na puni schema"
        (caller mora to detektirati).
    """

    text = raw_response.strip()

    # Strip code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```$", "", text)

    # Pokušaj JSON parse; ako fail-a, traži { ... } substring
    parsed: dict | None = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    if not isinstance(parsed, dict):
        return {}

    # Build case-insensitive lookup za validnost
    tables_lookup = {t.name.lower(): t for t in available_schema.tables}
    result: dict[str, list[str]] = {}

    for raw_table, raw_cols in parsed.items():
        if not isinstance(raw_table, str) or not isinstance(raw_cols, list):
            continue
        table_lower = raw_table.lower()
        if table_lower not in tables_lookup:
            continue
        real_table = tables_lookup[table_lower]
        real_table_name = real_table.name

        # Validne kolone
        cols_lookup = {c.name.lower(): c.name for c in real_table.columns}
        valid_cols = []
        for raw_col in raw_cols:
            if not isinstance(raw_col, str):
                continue
            real_col_name = cols_lookup.get(raw_col.lower())
            if real_col_name and real_col_name not in valid_cols:
                valid_cols.append(real_col_name)

        if valid_cols:
            result[real_table_name] = valid_cols

    return result


def filter_schema_to_columns(
    schema: DatabaseSchema,
    column_map: dict[str, list[str]],
) -> DatabaseSchema:
    """Vraća DatabaseSchema s samo navedenim kolonama po tablici.

    Slično ``filter_schema_to_tables`` ali na granularnijoj razini.
    Foreign keys se zadržavaju ako i source kolone i target kolone postoje
    u filtered shemi; inače se izostavljaju (LLM ne može više koristiti relaciju).

    Args:
        schema: pun DatabaseSchema.
        column_map: {table_name: [col_names]}.

    Returns:
        Novi DatabaseSchema. Ako je column_map prazan, vraća original schema
        (failsafe — bolje raditi s punim nego praznim).
    """

    if not column_map:
        return schema

    # Lowercase mapping za case-insensitive lookup
    column_map_lower: dict[str, set[str]] = {
        t.lower(): {c.lower() for c in cols} for t, cols in column_map.items()
    }

    new_tables: list[TableInfo] = []
    for table in schema.tables:
        wanted_cols = column_map_lower.get(table.name.lower())
        if wanted_cols is None:
            # Tablica nije u mapi — preskoči je (eqivalent table-level filter-u)
            continue

        # Filter columns — uvijek zadrži PK kolone (LLM-u koriste za join)
        filtered_cols: list[ColumnInfo] = []
        for col in table.columns:
            if col.is_primary_key or col.name.lower() in wanted_cols:
                filtered_cols.append(col)

        if not filtered_cols:
            # Edge case: ako nema niti jedne validne kolone, zadrži cijelu tablicu
            # (LLM dobiva bar mogućnost da je vidi).
            filtered_cols = list(table.columns)

        # Filter FK relations — zadrži samo ako i source i target kolone su uključene
        # (target tablica može biti izvan filtered set-a; tada FK izostavimo)
        filtered_fks = []
        for fk in table.foreign_keys:
            referred_lower = fk.referred_table.lower()
            if referred_lower not in column_map_lower:
                # Referenced tablica nije u filtered shemi; izostavi FK
                continue
            filtered_fks.append(fk)

        new_tables.append(
            TableInfo(
                name=table.name,
                columns=tuple(filtered_cols),
                foreign_keys=tuple(filtered_fks),
                sample_rows=table.sample_rows,  # zadrži sample data
            )
        )

    return DatabaseSchema(tables=tuple(new_tables), fetched_at=schema.fetched_at)
