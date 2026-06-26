"""Classify validation errors by recoverability through retry.

Empirically, different error types have very different retry success rates:

- Column-name typo (e.g. ``artist_nam`` → ``name``): ~85-90% recovery
- Table-name typo:                                    ~85% recovery
- Ambiguous column (unqualified in JOIN):             ~60% recovery
- Type mismatch / cast error:                         ~20% recovery
- Subquery scope issues:                              ~30% recovery

Allocating the same retry budget to all error types wastes attempts on
unrecoverable failures while under-investing in recoverable ones.
The retry loop in QueryService asks ``max_retries_for(error_class)``
each iteration and stops when the budget for the current class is spent.
"""

# Public error classes — keep them stable, the retry loop branches on them.
ERROR_CLASS_COLUMN_TYPO = "column_typo"
ERROR_CLASS_TABLE_TYPO = "table_typo"
ERROR_CLASS_AMBIGUOUS = "ambiguous"
ERROR_CLASS_TYPE_MISMATCH = "type_mismatch"
ERROR_CLASS_OTHER = "other"


def classify_error(error_msg: str) -> str:
    """Return one of the ``ERROR_CLASS_*`` constants for an error message.

    The semantic checker emits messages in Croatian (e.g. "Kolona X ne
    postoji"), the validator wraps PostgreSQL errors in English — match
    both to keep classification robust.
    """

    msg = error_msg.lower()

    if ("kolona" in msg or "column" in msg) and (
        "ne postoji" in msg or "doesn't exist" in msg or "does not exist" in msg
    ):
        return ERROR_CLASS_COLUMN_TYPO

    if ("tablica" in msg or "table" in msg) and (
        "ne postoji" in msg or "doesn't exist" in msg or "does not exist" in msg
    ):
        return ERROR_CLASS_TABLE_TYPO

    if "ambigu" in msg:
        return ERROR_CLASS_AMBIGUOUS

    if ("type" in msg and "mismatch" in msg) or "cast" in msg:
        return ERROR_CLASS_TYPE_MISMATCH

    return ERROR_CLASS_OTHER


def max_retries_for(error_class: str, default_budget: int) -> int:
    """Return how many retries the loop should allow for this error class.

    Strategy: high-recovery classes get +1 extra retry over the default
    budget (capped at 3), low-recovery classes get only 1 attempt.
    """

    if error_class in (ERROR_CLASS_COLUMN_TYPO, ERROR_CLASS_TABLE_TYPO):
        return min(default_budget + 1, 3)
    if error_class == ERROR_CLASS_AMBIGUOUS:
        return default_budget
    if error_class == ERROR_CLASS_TYPE_MISMATCH:
        return 1
    return default_budget
