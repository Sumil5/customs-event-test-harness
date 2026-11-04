"""XML message -> expected Oracle values, driven by the mapping file."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from lxml import etree

from customs_harness.reconciliation.compare import EXTRACTION_ERROR, UNMAPPED_CODE, FieldDiff
from customs_harness.reconciliation.mapping import ColumnSpec, Mapping
from customs_harness.reconciliation.rules import DERIVED_RULES


class ExtractionError(Exception):
    """The message cannot be reconciled at all (e.g. it has no business key)."""


class UnmappedCode(ValueError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"no lookup entry for {raw!r}")
        self.raw = raw


@dataclass
class ExpectedRecord:
    key: str
    correlation_id: str | None
    header: dict[str, Any]
    children: dict[str, dict[str, dict[str, Any]]]  # table -> item key -> column -> value
    problems: list[FieldDiff] = field(default_factory=list)


def coerce(raw: Any, spec: ColumnSpec) -> Any:
    """Turn a lexical value into the Python type it will be compared as."""
    if spec.type == "string":
        return str(raw)
    text = str(raw).strip()  # XSD collapses whitespace for non-string types
    match spec.type:
        case "integer":
            return int(Decimal(text))
        case "decimal":
            return Decimal(text)
        case "date":
            return date.fromisoformat(text[:10])
        case "datetime":
            value = datetime.fromisoformat(text)
            return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
        case "boolean":
            if text in ("true", "1"):
                return spec.true_value
            if text in ("false", "0"):
                return spec.false_value
            raise ValueError(f"not an xs:boolean: {text!r}")
    raise ValueError(f"unsupported type {spec.type}")


class Extractor:
    def __init__(self, mapping: Mapping) -> None:
        self.mapping = mapping
        self.ns = mapping.source.namespaces

    def _xpath(self, node: etree._Element, xpath: str) -> Any:
        result = node.xpath(xpath, namespaces=self.ns)
        if isinstance(result, list):
            if not result:
                return None
            first = result[0]
            if isinstance(first, etree._Element):
                return first.text if first.text is not None else ""
            return str(first)
        if isinstance(result, float):  # count(), sum()
            return int(result) if result.is_integer() else Decimal(str(result))
        return result

    def value(self, node: etree._Element, doc: etree._Element, spec: ColumnSpec) -> Any:
        raw = DERIVED_RULES[spec.derived](doc, self.ns) if spec.derived else self._xpath(node, spec.xpath)
        if raw is None:
            return None
        if spec.lookup:
            table = self.mapping.lookups[spec.lookup]
            code = str(raw).strip()
            if code not in table:
                raise UnmappedCode(code)
            raw = table[code]
        return coerce(raw, spec)

    def _row(
        self,
        node: etree._Element,
        doc: etree._Element,
        table: str,
        columns: dict[str, ColumnSpec],
        item_key: str | None,
    ) -> tuple[dict[str, Any], list[FieldDiff]]:
        row: dict[str, Any] = {}
        problems: list[FieldDiff] = []
        for column, spec in columns.items():
            try:
                row[column] = self.value(node, doc, spec)
            except UnmappedCode as exc:
                problems.append(FieldDiff(table, column, item_key, exc.raw, None, UNMAPPED_CODE, str(exc)))
            except (ValueError, InvalidOperation, ArithmeticError) as exc:
                problems.append(FieldDiff(table, column, item_key, None, None, EXTRACTION_ERROR, str(exc)))
        return row, problems

    def extract(self, doc: etree._Element) -> ExpectedRecord:
        key = self._xpath(doc, self.mapping.source.key)
        if not key:
            raise ExtractionError(f"no business key at {self.mapping.source.key}")
        header, problems = self._row(doc, doc, self.mapping.header.table, self.mapping.header.columns, None)

        children: dict[str, dict[str, dict[str, Any]]] = {}
        for child in self.mapping.children:
            items: dict[str, dict[str, Any]] = {}
            for node in doc.xpath(child.repeat, namespaces=self.ns):
                provisional = self._xpath(node, child.columns[child.item_key_column].xpath or "")
                row, item_problems = self._row(node, doc, child.table, child.columns, str(provisional))
                problems.extend(item_problems)
                item_key = str(row.get(child.item_key_column, provisional))
                if item_key in items:
                    problems.append(
                        FieldDiff(
                            child.table,
                            child.item_key_column,
                            item_key,
                            None,
                            None,
                            EXTRACTION_ERROR,
                            "duplicate item key in message",
                        )
                    )
                items[item_key] = row
            children[child.table] = items

        return ExpectedRecord(
            key=str(key).strip(),
            correlation_id=self._xpath(doc, self.mapping.source.correlation_id),
            header=header,
            children=children,
            problems=problems,
        )
