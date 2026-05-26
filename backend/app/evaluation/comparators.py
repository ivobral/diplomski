"""Usporedbe rezultata SQL upita — primarno za Execution Accuracy metriku.

BIRD benchmark koristi **Execution Accuracy** kao primarnu metriku — generirani
SQL je točan ako vrati isti rezultat kao gold SQL nad istom bazom.

"Isti rezultat" je netrivijalno: redovi mogu biti u različitom redoslijedu,
numeričke vrijednosti se mogu razlikovati u zadnjoj decimali (float-pointing),
NULL-ovi mogu biti različito reprezentirani. Ova logika razdvaja ono što je
**stvarno isti rezultat** od **slučajnih razlika reprezentacije**.

Pravilo iz BIRD literature: rezultati se uspoređuju kao **multiset** (set s
duplikatima, neuređen). Iznimka su upiti koji eksplicitno traže redoslijed
(`ORDER BY` u gold SQL-u) — tada redoslijed je dio rezultata.
"""

from __future__ import annotations

import math
from typing import Any

# Tolerancija za float-pointing usporedbe — 1e-6 je standard u BIRD eval-u.
_FLOAT_TOLERANCE = 1e-6


def rows_equal(
    predicted: list[list[Any]],
    gold: list[list[Any]],
    strict_order: bool = False,
) -> bool:
    """True ako su dva skupa redaka semantički ista.

    Args:
        predicted: rezultat izvršavanja generiranog SQL-a.
        gold: rezultat izvršavanja gold SQL-a.
        strict_order: ako je True, redoslijed redaka se mora podudarati
            (za pitanja s ORDER BY). Default False — multiset usporedba.

    Returns:
        True ako su rezultati semantički ekvivalentni.
    """

    if len(predicted) != len(gold):
        return False

    if strict_order:
        return all(_rows_match(p, g) for p, g in zip(predicted, gold, strict=True))

    # Multiset usporedba — sortiraj oba i usporedi po pozicijama. Sortiranje
    # je osjetljivo na tipove (None ne usporedo s int u Python-u 3), pa
    # serializiramo svaki redak u tuple stringova kao stable sort key.
    pred_sorted = sorted(predicted, key=_row_sort_key)
    gold_sorted = sorted(gold, key=_row_sort_key)
    return all(_rows_match(p, g) for p, g in zip(pred_sorted, gold_sorted, strict=True))


def _rows_match(a: list[Any], b: list[Any]) -> bool:
    """Dva retka jednake duljine se uspoređuju ćelijom-po-ćelijom."""

    if len(a) != len(b):
        return False
    return all(_values_match(x, y) for x, y in zip(a, b, strict=True))


def _values_match(a: Any, b: Any) -> bool:
    """Toleriraj numeričke razlike unutar _FLOAT_TOLERANCE; ostalo strogo."""

    # NULL handling — SQLite vraća None za NULL. Dva None su jednaka.
    if a is None or b is None:
        return a is None and b is None

    # Numerički slučaj — float prilagodljivo.
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        # Specijalno za NaN — Python NaN != NaN po IEEE 754, pa eksplicitno OK.
        if isinstance(a, float) and math.isnan(a) and isinstance(b, float) and math.isnan(b):
            return True
        try:
            return math.isclose(a, b, rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE)
        except (TypeError, ValueError):
            return a == b

    # Decimal.Decimal često dolazi iz SQLite NUMERIC kolona — konvertiraj u
    # float za usporedbu (gubitak preciznosti je u redu unutar tolerance).
    try:
        from decimal import Decimal
        if isinstance(a, Decimal) or isinstance(b, Decimal):
            return math.isclose(float(a), float(b), rel_tol=_FLOAT_TOLERANCE, abs_tol=_FLOAT_TOLERANCE)
    except Exception:
        pass

    # String/everything else — strogo.
    return a == b


def _row_sort_key(row: list[Any]) -> tuple[str, ...]:
    """Sortable key za redak — string reprezentacija svake ćelije.

    Razlog: heterogene liste (mix None/int/str/Decimal) ne mogu se direktno
    sortirati u Python 3 jer nema TotalOrder-a za miješane tipove. Stringove
    sortiramo deterministički ali rezultat sortiranja nije semantički bitan
    — bitno je samo da je *isti* za iste setove (deterministički).
    """

    return tuple("None" if v is None else repr(v) for v in row)
