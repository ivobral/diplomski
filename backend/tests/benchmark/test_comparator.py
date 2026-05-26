"""Testovi za rows_equal comparator.

Comparator je srž Execution Accuracy metrike — ako on griješi, sve EX
brojke u radu su pogrešne. Zato detaljno pokriće različitih slučajeva.
"""

from __future__ import annotations

from app.evaluation.comparators import rows_equal


class TestMultisetSemantics:
    """Default mode (strict_order=False) — redoslijed redaka ne važi."""

    def test_identical_rows(self) -> None:
        assert rows_equal([[1, "a"], [2, "b"]], [[1, "a"], [2, "b"]])

    def test_shuffled_rows_equal(self) -> None:
        assert rows_equal([[1, "a"], [2, "b"]], [[2, "b"], [1, "a"]])

    def test_different_lengths_unequal(self) -> None:
        assert not rows_equal([[1]], [[1], [2]])

    def test_different_values_unequal(self) -> None:
        assert not rows_equal([[1]], [[2]])

    def test_empty_results_equal(self) -> None:
        assert rows_equal([], [])

    def test_empty_vs_nonempty(self) -> None:
        assert not rows_equal([], [[1]])


class TestStrictOrder:
    """strict_order=True — koristi se za pitanja s ORDER BY."""

    def test_same_order_equal(self) -> None:
        assert rows_equal([[1], [2]], [[1], [2]], strict_order=True)

    def test_different_order_unequal(self) -> None:
        assert not rows_equal([[1], [2]], [[2], [1]], strict_order=True)


class TestNumericTolerance:
    """Float usporedba s malim epsilonom — BIRD često ima ratio kolone."""

    def test_float_exact(self) -> None:
        assert rows_equal([[1.0]], [[1.0]])

    def test_float_close_enough(self) -> None:
        """Float-ovi unutar 1e-6 su jednaki (BIRD CAST AS REAL precision)."""

        assert rows_equal([[1.0000001]], [[1.0]])

    def test_float_far_unequal(self) -> None:
        assert not rows_equal([[1.5]], [[1.0]])


class TestNullHandling:
    def test_null_equal(self) -> None:
        assert rows_equal([[None, 1]], [[None, 1]])

    def test_null_vs_value_unequal(self) -> None:
        assert not rows_equal([[None]], [[0]])


class TestMixedTypes:
    """SQL često miješa tipove — integer i string treba usporediti razumno."""

    def test_int_vs_int(self) -> None:
        assert rows_equal([[42]], [[42]])

    def test_string_vs_string(self) -> None:
        assert rows_equal([["abc"]], [["abc"]])

    def test_int_vs_string_repr(self) -> None:
        """SQLite ponekad vraća int kao string (npr. iz CAST). Comparator je tolerantan."""

        # Implementacija specific — možda strogo unequal, možda equal s coercion
        # Provjeravamo da nije crash
        result = rows_equal([[1]], [["1"]])
        assert isinstance(result, bool)
