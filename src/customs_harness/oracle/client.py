"""Read-only Oracle access for the harness (python-oracledb thin mode: no Instant Client)."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import oracledb

# NUMBER columns come back as Decimal, never float: exact comparison matters here.
oracledb.defaults.fetch_decimals = True

IN_LIST_LIMIT = 1000  # Oracle's maximum expressions in an IN list (ORA-01795)
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


def identifier(name: str) -> str:
    """Table/column names come from config files, so validate before building SQL."""
    if not _IDENTIFIER.match(name):
        raise ValueError(f"invalid Oracle identifier: {name!r}")
    return name.upper()


def chunks(values: Sequence[Any], size: int = IN_LIST_LIMIT) -> Iterator[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class OracleClient:
    def __init__(self, user: str, password: str, dsn: str) -> None:
        self.connection = oracledb.connect(user=user, password=password, dsn=dsn)

    @classmethod
    def from_settings(cls, settings: Any) -> OracleClient:
        return cls(settings.oracle_user, settings.oracle_password, settings.oracle_dsn)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> OracleClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def query(self, sql: str, binds: dict[str, Any] | Sequence[Any] | None = None) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.arraysize = 1000
            cursor.execute(sql, binds or {})
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor]

    def execute(self, sql: str, binds: dict[str, Any] | Sequence[Any] | None = None) -> int:
        """For test set-up/tear-down only (e.g. tamper tests). Commits immediately."""
        with self.connection.cursor() as cursor:
            cursor.execute(sql, binds or {})
            count = cursor.rowcount
        self.connection.commit()
        return count

    def fetch_in(self, table: str, column: str, values: Iterable[Any]) -> list[dict[str, Any]]:
        """SELECT * FROM table WHERE column IN (values), in chunks of 1000 binds."""
        table, column = identifier(table), identifier(column)
        rows: list[dict[str, Any]] = []
        unique = list(dict.fromkeys(values))
        for chunk in chunks(unique):
            placeholders = ", ".join(f":{i + 1}" for i in range(len(chunk)))
            rows.extend(self.query(f"SELECT * FROM {table} WHERE {column} IN ({placeholders})", list(chunk)))
        return rows

    def fetch_by_key(self, table: str, key_column: str, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        key_column = identifier(key_column)
        return {row[key_column]: row for row in self.fetch_in(table, key_column, keys)}

    def fetch_children(
        self, table: str, parent_key_column: str, parent_keys: Iterable[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """{parent_key: [child rows]}"""
        parent_key_column = identifier(parent_key_column)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.fetch_in(table, parent_key_column, parent_keys):
            grouped[row[parent_key_column]].append(row)
        return grouped

    def table_columns(self, table: str) -> set[str]:
        rows = self.query(
            "SELECT column_name FROM user_tab_columns WHERE table_name = :t", {"t": identifier(table)}
        )
        return {row["COLUMN_NAME"] for row in rows}
