"""Value mapping: traži stvarne DB vrijednosti za extracted entitete.

Drugi sub-step entity extraction pipeline-a. Uzima listu entitete iz LLM-a
i za svaki traži potencijalne kolone + točne vrijednosti u bazi.

Algoritam (per entity):
1. Za svaku text/string kolonu u shemi:
   - Provjeri case-insensitive equality: `LOWER(col) = LOWER(entity)` LIMIT 1
   - Ako pogoditi, vrati (table, col, actual_value)
2. Ako nema exact match-a (case-insensitive), pokušaj LIKE prefix:
   - `col LIKE 'entity%'` LIMIT 1
   - Korisno za "san bernardino" → "san bernardino county"
3. Filtri:
   - Skip kolone s prevelikim brojem distinct vrijednosti (vjerojatno PK/ID)
   - Skip čisto numeric kolone (entity je string)
   - Timeout per entity da ne zaustavi cijeli pipeline

Rezultat: dict {entity_text: list[(table, column, actual_value)]} sa
najboljim kandidatima. Prazan dict ako ništa ne matchat (LLM se onda
slobodno može oslanjati na svoju intuiciju).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.schema_inspector import DatabaseSchema

logger = logging.getLogger(__name__)

# Granice da mapping ne postane preskup:
_MAX_ENTITY_LENGTH = 80
_PER_ENTITY_TIMEOUT = 3.0     # sekundi per entity
_TOTAL_TIMEOUT = 15.0         # sekundi za sve entitete zajedno
_MAX_CANDIDATES_PER_ENTITY = 3


@dataclass(frozen=True, slots=True)
class ValueMapping:
    """Jedan match: entitet iz pitanja → DB vrijednost u tablici/koloni."""

    entity: str
    table: str
    column: str
    actual_value: str

    def to_hint(self) -> str:
        """Format za uvrstavanje u prompt."""

        if self.actual_value.lower() == self.entity.lower():
            return f"'{self.entity}' → {self.table}.{self.column}"
        # Različit case: jasno označi razliku
        return (
            f"'{self.entity}' → in {self.table}.{self.column} stored as '{self.actual_value}'"
        )


async def map_entities_to_values(
    entities: list[str],
    schema: DatabaseSchema,
    engine: AsyncEngine,
) -> list[ValueMapping]:
    """Mapira listu entitete na DB vrijednosti kroz lookup queries.

    Args:
        entities: lista string entitete iz entity extraction step-a.
        schema: filtered schema (samo kolone koje smo prošli kroz linking).
        engine: read-only AsyncEngine za bazu.

    Returns:
        Lista ValueMapping objekata, max _MAX_CANDIDATES_PER_ENTITY po entity-ju.
        Prazna lista znači "nema matcha — pretpostavi original".
    """

    if not entities:
        return []

    # Skupimo text kolone iz svih tablica (one koje bi mogle držati entitete)
    text_columns = _collect_text_columns(schema)
    if not text_columns:
        return []

    # Pripremi listu (entity, kandidat za lookup) parova
    valid_entities = [e for e in entities if _should_lookup_entity(e)]
    if not valid_entities:
        return []

    results: list[ValueMapping] = []

    try:
        async with asyncio.timeout(_TOTAL_TIMEOUT):
            async with engine.connect() as conn:
                for entity in valid_entities:
                    try:
                        matches = await asyncio.wait_for(
                            _find_entity_matches(conn, entity, text_columns),
                            timeout=_PER_ENTITY_TIMEOUT,
                        )
                    except TimeoutError:
                        continue
                    except Exception as exc:
                        logger.debug("value_mapper.failed", extra={
                            "entity": entity, "error": str(exc),
                        })
                        continue
                    results.extend(matches)
    except TimeoutError:
        # Total timeout — vrati što imamo do sad
        pass
    except Exception as exc:
        logger.debug("value_mapper.global_failed", extra={"error": str(exc)})

    return results


def _collect_text_columns(schema: DatabaseSchema) -> list[tuple[str, str]]:
    """Vrati listu (table_name, column_name) parova za string kolone.

    Detekcija "string column" preko data_type heuristike — SQLite tipovi mogu
    biti TEXT, VARCHAR, CHAR, BLOB (ne želimo BLOB).
    """

    pairs: list[tuple[str, str]] = []
    for table in schema.tables:
        for col in table.columns:
            dtype = col.data_type.upper()
            if "TEXT" in dtype or "CHAR" in dtype or "VARCHAR" in dtype:
                pairs.append((table.name, col.name))
    return pairs


def _should_lookup_entity(entity: str) -> bool:
    """Filtri za preskakanje (čisto numeric, prazno, jako dugačko)."""

    if not entity or len(entity) > _MAX_ENTITY_LENGTH:
        return False
    if entity.strip().isdigit():
        return False
    # Skip jednoslovne entitete (vjerojatno noise)
    if len(entity.strip()) < 2:
        return False
    return True


async def _find_entity_matches(
    conn,
    entity: str,
    text_columns: list[tuple[str, str]],
) -> list[ValueMapping]:
    """Pronađi top kandidate za entity kroz listu text kolona.

    Strategija:
    1. Case-insensitive exact match: LOWER(col) = LOWER(entity)
    2. Ako nema, LIKE 'entity%' (prefix match)
    3. Vrati prvih _MAX_CANDIDATES_PER_ENTITY
    """

    matches: list[ValueMapping] = []

    for table, column in text_columns:
        if len(matches) >= _MAX_CANDIDATES_PER_ENTITY:
            break

        quoted_table = f'"{table}"'
        quoted_col = f'"{column}"'

        # 1. Case-insensitive exact
        try:
            q = text(
                f"SELECT DISTINCT {quoted_col} FROM {quoted_table} "
                f"WHERE LOWER({quoted_col}) = LOWER(:e) LIMIT 1"
            )
            result = await conn.execute(q, {"e": entity})
            row = result.fetchone()
            if row is not None:
                matches.append(ValueMapping(
                    entity=entity, table=table, column=column,
                    actual_value=str(row[0]),
                ))
                continue  # Najveći score — ne tražimo prefix u istoj koloni
        except Exception:
            continue

        # 2. Prefix LIKE — samo ako entity ima >= 3 znaka da izbjegnemo lažne match-eve
        if len(entity) >= 3:
            try:
                q = text(
                    f"SELECT DISTINCT {quoted_col} FROM {quoted_table} "
                    f"WHERE LOWER({quoted_col}) LIKE LOWER(:e) || '%' LIMIT 1"
                )
                result = await conn.execute(q, {"e": entity})
                row = result.fetchone()
                if row is not None:
                    val = str(row[0])
                    # Skip ako je prefix sam (već uhvaćeno gore), inače je novi info
                    if val.lower() != entity.lower():
                        matches.append(ValueMapping(
                            entity=entity, table=table, column=column,
                            actual_value=val,
                        ))
            except Exception:
                continue

    return matches


def format_value_hints(mappings: list[ValueMapping]) -> str:
    """Formatira value mappings za uvrstavanje u glavni prompt.

    Vraća tekstualni blok spreman za prompt; prazan string ako nema mappings.
    """

    if not mappings:
        return ""

    lines = ["Value lookups (use these EXACT values in WHERE conditions):"]
    for m in mappings:
        lines.append(f"  - {m.to_hint()}")
    return "\n".join(lines)
