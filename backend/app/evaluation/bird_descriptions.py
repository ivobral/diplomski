"""Loader BIRD column descriptions iz CSV-ova.

BIRD dataset uz svaku bazu prilaže ``database_description/<table>.csv`` s
human-readable opisom svake kolone — što kolona "stvarno znači", kako se
mapiraju vrijednosti, primjena formata datuma itd.

Bez tih opisa LLM mora pogađati semantiku iz imena kolone (npr. "A11"
nije očito da je "average salary"). S opisima, accuracy raste — vidi
DAIL-SQL i druge BIRD-ove SOTA radove.

CSV format (po BIRD specifikaciji):
    original_column_name,column_name,column_description,data_format,value_description
    cds,,California Department Schools,text,
    A11,,average salary in district,integer,USD/year

Implementacija je defenzivna prema BOM/encoding čudnostima koje CSV-ovi
imaju (file počinje s UTF-8 BOM, neki s Windows-1252, …).
"""

from __future__ import annotations

import csv
from pathlib import Path


def load_column_descriptions(db_id: str, dataset_path: Path) -> dict[tuple[str, str], str]:
    """Učitaj sve CSV-ove iz ``database_description`` foldera za zadanu BIRD bazu.

    Args:
        db_id: ime BIRD baze (npr. ``"california_schools"``).
        dataset_path: korijenski folder BIRD-a (``/app/data/bird_mini``).

    Returns:
        Dict {(table_name, column_name): description_text}. Prazan ako
        folder ne postoji ili je sadržaj corrupt. Ključ je case-sensitive
        s originalnim imenima kakve i schema_inspector vraća.
    """

    descriptions: dict[tuple[str, str], str] = {}
    description_dir = dataset_path / "databases" / db_id / "database_description"
    if not description_dir.is_dir():
        return descriptions

    for csv_path in description_dir.glob("*.csv"):
        # Ime CSV file-a = ime tablice (bez .csv ekstenzije)
        table_name = csv_path.stem

        # BIRD CSV-ovi često imaju UTF-8 BOM (﻿) na prvom redu;
        # `utf-8-sig` ga tiho jede. Neki su Windows-1252 — pa fallback.
        try:
            text = csv_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = csv_path.read_text(encoding="cp1252", errors="replace")

        try:
            reader = csv.DictReader(text.splitlines())
            for row in reader:
                col_name = (row.get("original_column_name") or "").strip()
                if not col_name:
                    continue
                # Kombiniramo `column_description` + `value_description` u jedan
                # opis — oba su korisna LLM-u. Filtriramo "unuseful" sentinel
                # koji BIRD ponekad koristi za stupce koji nemaju značenja.
                col_desc = (row.get("column_description") or "").strip()
                val_desc = (row.get("value_description") or "").strip()
                expanded_name = (row.get("column_name") or "").strip()

                if val_desc.lower() == "unuseful":
                    continue

                parts: list[str] = []
                if expanded_name and expanded_name.lower() != col_name.lower():
                    parts.append(expanded_name)
                if col_desc and col_desc.lower() != col_name.lower():
                    parts.append(col_desc)
                if val_desc:
                    parts.append(f"values: {val_desc}")

                if parts:
                    descriptions[(table_name, col_name)] = "; ".join(parts)
        except csv.Error:
            # Bad CSV — preskoči samo tu tablicu, ne ruši sve
            continue

    return descriptions
