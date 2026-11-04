"""Type-aware, field-by-field comparison - plus a best guess at *why* values differ.

The guess ("rounded to 2 dp", "truncated to 100 chars", "non-ASCII characters
lost") turns a list of thousands of mismatches into a handful of defect
signatures, which is what a developer needs to go and fix the bug.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any

from customs_harness.reconciliation.mapping import ColumnSpec

VALUE_MISMATCH = "VALUE_MISMATCH"
MISSING_ROW = "MISSING_ROW"
UNEXPECTED_ROW = "UNEXPECTED_ROW"
UNMAPPED_CODE = "UNMAPPED_CODE"
EXTRACTION_ERROR = "EXTRACTION_ERROR"


@dataclass(frozen=True)
class FieldDiff:
    table: str
    column: str
    item_key: str | None
    expected: Any
    actual: Any
    kind: str = VALUE_MISMATCH
    hint: str | None = None

    @property
    def location(self) -> str:
        return f"{self.table}.{self.column}"

    @property
    def path(self) -> str:
        item = f"[{self.item_key}]" if self.item_key is not None else ""
        return f"{self.table}{item}.{self.column}"


def normalise_actual(value: Any, spec: ColumnSpec) -> Any:
    """Bring an Oracle value into the same Python type as the expected value."""
    if value is None:
        return None
    match spec.type:
        case "string" | "boolean":
            return str(value)
        case "integer":
            return int(value)
        case "decimal":
            return value if isinstance(value, Decimal) else Decimal(str(value))
        case "date":
            return value.date() if isinstance(value, datetime) else value
        case "datetime":
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value


def values_equal(expected: Any, actual: Any, spec: ColumnSpec) -> bool:
    if spec.type in ("string", "boolean"):
        return (expected or None) == (actual or None)  # Oracle stores '' as NULL
    if expected is None or actual is None:
        return expected is None and actual is None
    if spec.type == "decimal" and spec.tolerance is not None:
        return abs(expected - actual) <= spec.tolerance
    return expected == actual  # Decimal("12.5") == Decimal("12.500")


def ascii_fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def diagnose(expected: Any, actual: Any) -> str | None:
    """Best-effort label for a mismatch, used to group defects in reports."""
    if actual in (None, "") and expected not in (None, ""):
        return "value not stored (NULL)"
    if expected in (None, "") and actual not in (None, ""):
        return "value stored but message had none"
    if isinstance(expected, Decimal) and isinstance(actual, Decimal):
        # Most precise explanation first: rounding 12.296 to 2 dp gives 12.30, which also equals 12.3.
        for places in range(3, -1, -1):
            unit = Decimal(1).scaleb(-places)
            if actual in (expected.quantize(unit, ROUND_HALF_UP), expected.quantize(unit, ROUND_HALF_EVEN)):
                return f"rounded to {places} dp"
            if actual == expected.quantize(unit, ROUND_DOWN):
                return f"truncated to {places} dp"
        if actual == -expected:
            return "sign flipped"
        return None
    if isinstance(expected, str) and isinstance(actual, str):
        if ascii_fold(expected) == actual:  # before truncation: "Strauß" -> "Strau" is lost, not cut
            return "non-ASCII characters lost"
        if expected.startswith(actual):
            return f"truncated to {len(actual)} chars"
        if expected.strip() == actual.strip():
            return "whitespace differs"
        if expected.casefold() == actual.casefold():
            return "letter case differs"
        return None
    if isinstance(expected, datetime) and isinstance(actual, datetime):
        if expected.replace(microsecond=0) == actual:
            return "fractional seconds lost"
        seconds = (actual - expected).total_seconds()
        if seconds and seconds % 1800 == 0:
            return f"shifted by {seconds / 3600:+g}h (time zone?)"
        return None
    if isinstance(expected, date) and isinstance(actual, date):
        return f"off by {(actual - expected).days:+d} day(s)"
    return None


def compare_row(
    table: str,
    expected: dict[str, Any],
    actual_row: dict[str, Any],
    columns: dict[str, ColumnSpec],
    item_key: str | None = None,
) -> list[FieldDiff]:
    diffs = []
    for column, spec in columns.items():
        if column not in expected:  # could not be extracted; reported separately
            continue
        actual = normalise_actual(actual_row.get(column), spec)
        if not values_equal(expected[column], actual, spec):
            hint = diagnose(expected[column], actual)
            if hint is None and spec.derived:
                hint = f"service disagrees with the '{spec.derived}' rule"
            diffs.append(FieldDiff(table, column, item_key, expected[column], actual, hint=hint))
    return diffs


def compare_rows_by_key(
    table: str,
    expected_items: dict[str, dict[str, Any]],
    actual_items: dict[str, dict[str, Any]],
    columns: dict[str, ColumnSpec],
) -> list[FieldDiff]:
    """Compare child rows matched on their item key, reporting missing and extra rows."""
    diffs = []
    for item_key, expected in expected_items.items():
        actual = actual_items.get(item_key)
        if actual is None:
            diffs.append(FieldDiff(table, "*", item_key, "row", None, MISSING_ROW, "row not stored"))
            continue
        diffs.extend(compare_row(table, expected, actual, columns, item_key))
    for item_key in sorted(actual_items.keys() - expected_items.keys()):
        diffs.append(FieldDiff(table, "*", item_key, None, "row", UNEXPECTED_ROW, "row not in message"))
    return diffs
