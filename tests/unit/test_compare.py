"""Field comparison semantics and defect diagnosis."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from customs_harness.reconciliation.compare import (
    MISSING_ROW,
    UNEXPECTED_ROW,
    compare_row,
    compare_rows_by_key,
    diagnose,
    normalise_actual,
    values_equal,
)
from customs_harness.reconciliation.mapping import ColumnSpec

STRING = ColumnSpec(xpath="x")
DECIMAL = ColumnSpec(xpath="x", type="decimal")
DATETIME = ColumnSpec(xpath="x", type="datetime")
DATE = ColumnSpec(xpath="x", type="date")


def test_decimals_compare_numerically():
    assert values_equal(Decimal("12.5"), Decimal("12.500"), DECIMAL)
    assert not values_equal(Decimal("12.345"), Decimal("12.35"), DECIMAL)


def test_tolerance_is_opt_in():
    spec = ColumnSpec(xpath="x", type="decimal", tolerance=Decimal("0.01"))
    assert values_equal(Decimal("10.004"), Decimal("10.00"), spec)


def test_empty_string_equals_null_because_oracle_stores_it_that_way():
    assert values_equal("", None, STRING)
    assert values_equal(None, None, STRING)
    assert not values_equal("A", None, STRING)


def test_whitespace_is_significant_for_strings():
    assert not values_equal("Belfast ", "Belfast", STRING)


def test_naive_oracle_timestamps_are_treated_as_utc():
    actual = normalise_actual(datetime(2026, 11, 4, 9, 30, 0, 123000), DATETIME)
    assert values_equal(datetime(2026, 11, 4, 9, 30, 0, 123000, tzinfo=UTC), actual, DATETIME)


def test_oracle_date_columns_compare_as_dates():
    assert normalise_actual(datetime(2026, 11, 20, 0, 0), DATE) == date(2026, 11, 20)


@pytest.mark.parametrize(
    ("expected", "actual", "hint"),
    [
        (Decimal("12.345"), Decimal("12.35"), "rounded to 2 dp"),
        (Decimal("12.296"), Decimal("12.30"), "rounded to 2 dp"),  # not "1 dp", although 12.30 == 12.3
        (Decimal("12.379"), Decimal("12.3"), "truncated to 1 dp"),  # rounding would give 12.4
        (Decimal("12.5"), Decimal("-12.5"), "sign flipped"),
        ("Assorted mixed consignment", "Assorted", "truncated to 8 chars"),
        ("Ó Briain Agri Teoranta", "O Briain Agri Teoranta", "non-ASCII characters lost"),
        ("Strauß Logistik", "Strau Logistik", "non-ASCII characters lost"),
        ("Strauß", "Strau", "non-ASCII characters lost"),
        ("Belfast", " Belfast ", "whitespace differs"),
        ("Belfast", "BELFAST", "letter case differs"),
        ("XI123", None, "value not stored (NULL)"),
        (None, "XI123", "value stored but message had none"),
        (
            datetime(2026, 11, 4, 9, 30, 0, 123000, tzinfo=UTC),
            datetime(2026, 11, 4, 9, 30, 0, tzinfo=UTC),
            "fractional seconds lost",
        ),
        (
            datetime(2026, 7, 1, 9, 30, tzinfo=UTC),
            datetime(2026, 7, 1, 10, 30, tzinfo=UTC),
            "shifted by +1h (time zone?)",
        ),
        (date(2026, 11, 20), date(2026, 11, 19), "off by -1 day(s)"),
        ("GREEN", "RED", None),
    ],
)
def test_diagnosis(expected, actual, hint):
    assert diagnose(expected, actual) == hint


def test_compare_row_reports_only_differing_columns():
    columns = {"A": STRING, "B": DECIMAL, "C": STRING}
    diffs = compare_row(
        "T", {"A": "x", "B": Decimal("1.5"), "C": "z"}, {"A": "x", "B": Decimal("1.50"), "C": "Z"}, columns
    )
    assert [(d.column, d.hint) for d in diffs] == [("C", "letter case differs")]


def test_columns_that_failed_extraction_are_skipped():
    assert compare_row("T", {}, {"A": "anything"}, {"A": STRING}) == []


def test_derived_column_mismatch_names_the_rule():
    spec = ColumnSpec(derived="lane")
    (diff,) = compare_row("DECLARATION", {"LANE": "RED"}, {"LANE": "GREEN"}, {"LANE": spec})
    assert diff.hint == "service disagrees with the 'lane' rule"


def test_child_rows_are_matched_by_item_key():
    columns = {"ITEM_NUMBER": ColumnSpec(xpath="n", type="integer"), "D": STRING}
    expected = {"1": {"ITEM_NUMBER": 1, "D": "a"}, "2": {"ITEM_NUMBER": 2, "D": "b"}}
    actual = {"1": {"ITEM_NUMBER": 1, "D": "a"}, "3": {"ITEM_NUMBER": 3, "D": "c"}}
    diffs = compare_rows_by_key("GOODS_ITEM", expected, actual, columns)
    assert {(d.item_key, d.kind) for d in diffs} == {("2", MISSING_ROW), ("3", UNEXPECTED_ROW)}
    assert {d.path for d in diffs} == {"GOODS_ITEM[2].*", "GOODS_ITEM[3].*"}
