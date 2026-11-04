"""Typed model of a reconciliation mapping file (see config/mappings/*.yaml)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

ColumnType = Literal["string", "integer", "decimal", "date", "datetime", "boolean"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnSpec(_Strict):
    xpath: str | None = None
    derived: str | None = None
    type: ColumnType = "string"
    lookup: str | None = None
    true_value: str = "true"
    false_value: str = "false"
    tolerance: Decimal | None = None

    @model_validator(mode="after")
    def _one_source(self) -> ColumnSpec:
        if (self.xpath is None) == (self.derived is None):
            raise ValueError("a column needs exactly one of 'xpath' or 'derived'")
        return self


class HeaderTable(_Strict):
    table: str
    key_column: str
    timestamp_column: str | None = None  # enables the two-way (orphan) check
    columns: dict[str, ColumnSpec]


class ChildTable(_Strict):
    table: str
    parent_key_column: str
    repeat: str
    item_key_column: str
    columns: dict[str, ColumnSpec]

    @model_validator(mode="after")
    def _item_key_mapped(self) -> ChildTable:
        if self.item_key_column not in self.columns:
            raise ValueError(f"item_key_column {self.item_key_column} must be one of the mapped columns")
        return self


class Source(_Strict):
    topic: str
    rejected_topic: str | None = None
    namespaces: dict[str, str]
    key: str
    correlation_id: str


class Mapping(_Strict):
    name: str
    description: str = ""
    source: Source
    lookups: dict[str, dict[str, str | int]] = {}
    header: HeaderTable
    children: list[ChildTable] = []

    @model_validator(mode="after")
    def _lookups_exist(self) -> Mapping:
        for table in [self.header, *self.children]:
            for column, spec in table.columns.items():
                if spec.lookup and spec.lookup not in self.lookups:
                    raise ValueError(f"{table.table}.{column}: unknown lookup '{spec.lookup}'")
        return self

    @classmethod
    def load(cls, path: Path) -> Mapping:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))
