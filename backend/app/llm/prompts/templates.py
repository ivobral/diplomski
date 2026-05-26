"""Template stringovi za prompt construction.

Svi system / user template stringovi i schema formatter žive ovdje, kao
modul-level konstante. Razlog: prompt je **specifikacija ponašanja LLM-a**
i jedna od ključnih varijabli koje diplomski rad analizira — drži se na
jednom mjestu, lakše za čitanje, modificiranje i dokumentiranje u radu.

Promptovi su pisani na engleskom. LLM-ovi (Claude, GPT, Ollama modeli)
rade značajno bolje na engleskom, posebno za tehnička pravila i SQL —
to je dobro dokumentirano u literaturi. Pitanje korisnika može biti
na bilo kojem jeziku; SQL identifikatori i ključne riječi su engleski.
"""

from __future__ import annotations

from app.db.schema_inspector import DatabaseSchema

# ----------------------------------------------------------------------
# System prompts — po jedan po SQL dialect-u.
# Tri pravila su zajednička (struktura, sigurnost, samo SELECT); dialect-
# specifična pravila idu o relevantnoj sekciji prompta da LLM odmah cilja
# pravu sintaksu (NOW vs datetime('now'), ILIKE vs LIKE itd.).
# ----------------------------------------------------------------------

_COMMON_RULES = """\
STRICT RULES (must be followed exactly):
1. Generate ONLY SQL. No prose, no explanations, no markdown code fences.
2. Use ONLY SELECT statements. NEVER generate INSERT, UPDATE, DELETE, DROP,
   ALTER, CREATE, TRUNCATE, GRANT, REVOKE, or any other statement type.
3. Generate exactly ONE statement. No semicolons except optionally at the end.
4. Use ONLY tables and columns that appear in the provided schema. Never
   invent table or column names.
5. If the question cannot be answered with the given schema, return:
   SELECT 'unable to answer' AS error
6. Prefer explicit JOINs with ON conditions over implicit (comma) joins.
7. Qualify columns with table aliases when joining multiple tables.
"""

SYSTEM_PROMPT_POSTGRES = f"""\
You are a careful and precise SQL generator for a PostgreSQL database.

Your job: given a question in natural language and (optionally) a database
schema, return a single SQL SELECT statement that answers the question.

{_COMMON_RULES}
8. Use PostgreSQL dialect — e.g., NOW(), CURRENT_TIMESTAMP, DATE_TRUNC, ILIKE,
   EXTRACT(YEAR FROM date), INTERVAL '1 year', cast with `::TYPE`.
"""

_SQLITE_FEW_SHOT = """\
Examples of question → SQL (SQLite dialect):

# Example 1 — aggregation with JOIN, single answer column
Schema excerpt:
  Table customer: customer_id PK, country TEXT
  Table invoice:  invoice_id PK, customer_id, total REAL
Question: Which country generates the most invoice revenue?
SQL:
SELECT c.country
FROM customer AS c
JOIN invoice AS i ON i.customer_id = c.customer_id
GROUP BY c.country
ORDER BY SUM(i.total) DESC
LIMIT 1

# Example 2 — quoted column, date filtering, COUNT only
Question: How many schools opened after 2010?
Schema excerpt:
  Table school: school_id PK, "Open Date" TEXT, "School Type" TEXT
SQL:
SELECT COUNT(*)
FROM school
WHERE STRFTIME('%Y', "Open Date") > '2010'

# Example 3 — "X and their Y": return BOTH in the order question states
Schema excerpt:
  Table artist: artist_id PK, name TEXT
  Table album:  album_id PK, artist_id, title TEXT
Question: List artists and the number of albums they have, top 5.
SQL:
SELECT a.name, COUNT(b.album_id) AS album_count
FROM artist AS a
JOIN album AS b ON b.artist_id = a.artist_id
GROUP BY a.name
ORDER BY album_count DESC
LIMIT 5

# Example 4 — evidence-guided filter
Question: How many female clients live in the East Bohemia district?
Evidence: A3 column = district name; gender = 'F' for female
Schema excerpt:
  Table client: client_id PK, gender TEXT, district_id
  Table district: district_id PK, A3 TEXT
SQL:
SELECT COUNT(*)
FROM client AS c
JOIN district AS d ON d.district_id = c.district_id
WHERE c.gender = 'F' AND d.A3 = 'east Bohemia'
"""

SYSTEM_PROMPT_SQLITE = f"""\
You are a careful and precise SQL generator for a SQLite database.

Your job: given a question in natural language and (optionally) a database
schema, return a single SQL SELECT statement that answers the question.

{_COMMON_RULES}
8. Use SQLite dialect — e.g., date('now'), datetime('now'), STRFTIME('%Y', col),
   LIKE (not ILIKE — SQLite is case-insensitive by default for ASCII),
   `||` for string concat, CAST(col AS INTEGER).
9. Quote column names with spaces or special characters using double quotes:
   "Free Meal Count (K-12)" not Free Meal Count (K-12).
10. Pay attention to sample data rows (if provided) — they show exact case
    and format of values to match in WHERE conditions.
11. Return ONLY the minimum columns needed to directly answer the question.
    Do NOT add extra columns for context, explanation, or "convenience"
    (e.g., if asked "what are the codes", return only the code column —
    not the code plus name plus description). Extra columns mark a
    correct answer as wrong in automated grading.
12. If the question asks for "the X of Y", return X. If it asks "Y and their X",
    return Y, X — in that order. Match the question's stated structure.
13. If the question asks for evidence/hints that map natural-language terms
    to specific columns or filters, USE THEM. The hint is the bridge
    between human intent and the actual schema.

{_SQLITE_FEW_SHOT}
"""

# Backward-compat alias — postojeći import-i koji koriste SYSTEM_PROMPT
# i dalje rade (uperi na PostgreSQL koji je default Chinook dialect).
SYSTEM_PROMPT = SYSTEM_PROMPT_POSTGRES


def get_system_prompt(dialect: str) -> str:
    """Vrati system prompt za zadani SQL dialect.

    Args:
        dialect: ``"postgres"`` ili ``"sqlite"``. Sve drugo daje ValueError.
    """

    if dialect == "postgres":
        return SYSTEM_PROMPT_POSTGRES
    if dialect == "sqlite":
        return SYSTEM_PROMPT_SQLITE
    raise ValueError(f"Nepoznat SQL dialect za system prompt: {dialect!r}")


# ----------------------------------------------------------------------
# User prompt templates — different strategies use different ones.
# ----------------------------------------------------------------------

USER_TEMPLATE_QUESTION_ONLY = """\
Question: {question}

Generate the SQL:"""


USER_TEMPLATE_WITH_SCHEMA = """\
Database schema:
{schema}

Question: {question}

Generate the SQL:"""


USER_TEMPLATE_WITH_RELATIONS = """\
Database schema (tables, columns, and relations):
{schema}

Question: {question}

Generate the SQL:"""


# Evidence — koristi se isključivo u Strategiji D s BIRD pitanjima.
# Evidence je expert-written hint koji objašnjava semantičku mapu pitanja
# u SQL termine (npr. "eligible free rate = Free Meal Count / Enrollment").
# Bez ovoga LLM mora pogađati ekvivalencije; s njim, dobiva
# "domain expert" pomoć i točnost značajno raste (vidi BIRD radove).
USER_TEMPLATE_WITH_RELATIONS_AND_EVIDENCE = """\
Database schema (tables, columns, and relations):
{schema}

Expert hint (use this to understand the question's intent):
{evidence}

Question: {question}

Generate the SQL:"""


# Najobogaćeniji template — uključuje i decomposition steps generirane
# planning pre-step LLM-om. Koristi se samo za D strategiju kad je
# decomposition uspješan; inače fallback na WITH_RELATIONS_AND_EVIDENCE.
USER_TEMPLATE_WITH_DECOMPOSITION = """\
Database schema (tables, columns, and relations):
{schema}

Expert hint (use this to understand the question's intent):
{evidence}

Planning steps (follow these in order when writing SQL):
{decomposition}

Question: {question}

Generate the SQL:"""


# ----------------------------------------------------------------------
# Retry template — sent when validation failed (parse / semantic).
# Includes the failed SQL, exact errors, and current schema so the LLM has
# everything needed to fix the query.
# ----------------------------------------------------------------------

RETRY_TEMPLATE = """\
Your previous SQL failed validation. Fix it and return a corrected SELECT.

Previous SQL:
{previous_sql}

Validation errors:
{errors}

Database schema (use ONLY these tables and columns):
{schema}

Original question: {question}

Generate the corrected SQL:"""


# ----------------------------------------------------------------------
# Schema formatter — produces a compact textual schema for the prompt.
# ----------------------------------------------------------------------


def format_schema_for_prompt(
    schema: DatabaseSchema,
    include_relations: bool,
    include_sample_rows: bool = False,
    column_descriptions: dict[tuple[str, str], str] | None = None,
) -> str:
    """Konvertira ``DatabaseSchema`` u tekstualni opis za prompt.

    Format je "CREATE TABLE-like" jer je to oblik koji LLM-ovi razumiju
    najprirodnije (vidjeli su tisuće DDL-ova u training data). Foreign key
    relacije i sample redovi dolaze ispod tablice kao komentari.

    Args:
        schema: dohvaćena shema baze.
        include_relations: ako True, ispod kolona dodaje FK reference.
        include_sample_rows: ako True i tablica ima ``sample_rows`` (iz
            schema introspection s ``include_sample_rows=True``), prikazuje
            ih kao primjer podataka.

    Returns:
        Multi-line string spreman za ubacivanje u prompt template.
    """

    lines: list[str] = []

    for table in schema.tables:
        # Header s imenom tablice.
        lines.append(f"Table {table.name}:")

        # Kolone — ime, tip, nullable/PK marker + (BIRD) inline description.
        for col in table.columns:
            markers: list[str] = []
            if col.is_primary_key:
                markers.append("PK")
            if not col.nullable:
                markers.append("NOT NULL")
            marker_str = f" [{', '.join(markers)}]" if markers else ""

            # Optional column description iz BIRD CSV-a. Pojavljuje se kao
            # inline komentar nakon tipa — LLM vidi "Column foo INT — meaning".
            desc = ""
            if column_descriptions is not None:
                desc_text = column_descriptions.get((table.name, col.name))
                if desc_text:
                    desc = f"  -- {desc_text}"

            lines.append(f"  - {col.name} {col.data_type}{marker_str}{desc}")

        # FK relacije — samo ako su tražene (strategije C i D).
        if include_relations and table.foreign_keys:
            for fk in table.foreign_keys:
                src = ", ".join(fk.constrained_columns)
                dst = ", ".join(fk.referred_columns)
                lines.append(f"  FK: ({src}) -> {fk.referred_table}({dst})")

        # Sample rows — pomažu s case sensitivity i value awareness.
        # Format: "Sample: (v1, v2, ...) | (v3, v4, ...) | ..."
        if include_sample_rows and table.sample_rows:
            col_names = ", ".join(c.name for c in table.columns)
            lines.append(f"  Sample data ({col_names}):")
            for row in table.sample_rows:
                # repr da se stringovi pokažu s navodnicima — LLM tako jasno
                # vidi da je "north Bohemia" lowercase string, npr.
                row_repr = ", ".join(repr(v) for v in row)
                lines.append(f"    ({row_repr})")

        lines.append("")  # blank line između tablica radi čitljivosti

    return "\n".join(lines).rstrip()
