"""Reconciliation: every message on a topic vs what is stored in Oracle.

For each message the engine answers two questions:

* Completeness - is it accounted for? Stored in Oracle, or dead-lettered with a
  reason. Anything else is MISSING: the pipeline lost it.
* Accuracy - if stored, does every mapped column match the message, field by
  field (and every goods item, row by row)?

Messages are processed in batches so Oracle is queried with one IN-list per
table per batch, which keeps thousands of messages to a few dozen queries.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from lxml import etree

from customs_harness.config import Settings
from customs_harness.kafka import Record, read_topic
from customs_harness.oracle import OracleClient
from customs_harness.oracle.client import identifier
from customs_harness.reconciliation.compare import (
    FieldDiff,
    compare_row,
    compare_rows_by_key,
    normalise_actual,
)
from customs_harness.reconciliation.extract import ExpectedRecord, ExtractionError, Extractor
from customs_harness.reconciliation.mapping import Mapping
from customs_harness.xml import parse


class Status(StrEnum):
    MATCHED = "MATCHED"  # stored, every field matches
    MISMATCHED = "MISMATCHED"  # stored, at least one field differs
    MISSING = "MISSING"  # neither stored nor dead-lettered: lost
    REJECTED = "REJECTED"  # dead-lettered with a reason: accounted for
    UNPARSEABLE = "UNPARSEABLE"  # cannot be read and was not dead-lettered


@dataclass
class MessageResult:
    status: Status
    correlation_id: str | None
    key: str | None
    partition: int
    offset: int
    diffs: list[FieldDiff] = field(default_factory=list)
    detail: str | None = None


@dataclass
class ReconciliationReport:
    mapping: str
    topic: str
    run_id: str | None
    since: datetime | None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    messages_read: int = 0
    duplicate_messages: int = 0
    status_counts: Counter = field(default_factory=Counter)
    rejection_codes: Counter = field(default_factory=Counter)
    column_mismatches: Counter = field(default_factory=Counter)
    defect_signatures: Counter = field(default_factory=Counter)
    results: list[MessageResult] = field(default_factory=list)  # everything except MATCHED
    orphans: list[str] = field(default_factory=list)
    two_way: bool = False

    def add(self, result: MessageResult) -> None:
        self.status_counts[result.status] += 1
        if result.status is Status.MATCHED:
            return
        self.results.append(result)
        if result.status is Status.REJECTED and result.detail:
            self.rejection_codes[result.detail.split(":", 1)[0]] += 1
        for diff in result.diffs:
            self.column_mismatches[diff.location] += 1
            self.defect_signatures[(diff.location, diff.hint or diff.kind)] += 1

    def count(self, status: Status) -> int:
        return self.status_counts.get(status, 0)

    @property
    def discrepancies(self) -> int:
        return (
            self.count(Status.MISMATCHED)
            + self.count(Status.MISSING)
            + self.count(Status.UNPARSEABLE)
            + len(self.orphans)
        )

    @property
    def passed(self) -> bool:
        return self.discrepancies == 0

    @property
    def duration_seconds(self) -> float:
        return ((self.finished_at or datetime.now(UTC)) - self.started_at).total_seconds()


class ReconciliationEngine:
    def __init__(
        self, mapping: Mapping, settings: Settings, oracle: OracleClient, batch_size: int = 500
    ) -> None:
        self.mapping = mapping
        self.settings = settings
        self.oracle = oracle
        self.batch_size = batch_size
        self.extractor = Extractor(mapping)
        self._check_mapping_against_database()

    def _check_mapping_against_database(self) -> None:
        """Fail fast if the mapping names columns the database does not have (schema drift)."""
        problems = []
        for table in [self.mapping.header, *self.mapping.children]:
            existing = self.oracle.table_columns(table.table)
            if not existing:
                problems.append(f"table {table.table} not found")
                continue
            wanted = set(table.columns) | {getattr(table, "key_column", None) or table.parent_key_column}
            problems += [f"{table.table}.{c} not found" for c in sorted(wanted - existing)]
        if problems:
            raise ValueError("mapping does not match the database: " + "; ".join(problems))

    def run(
        self, since: datetime | None = None, run_id: str | None = None, two_way: bool = False
    ) -> ReconciliationReport:
        if two_way and (run_id or not self.mapping.header.timestamp_column):
            raise ValueError("two-way check needs a time window (no run_id) and header.timestamp_column")
        report = ReconciliationReport(
            self.mapping.name, self.mapping.source.topic, run_id, since, two_way=two_way
        )
        rejections = self._rejections(since, run_id)
        seen_keys: set[str] = set()
        batch: list[Record] = []
        for record in read_topic(
            self.settings.kafka_bootstrap_servers, self.mapping.source.topic, since, run_id
        ):
            report.messages_read += 1
            batch.append(record)
            if len(batch) >= self.batch_size:
                self._reconcile_batch(batch, rejections, seen_keys, report)
                batch = []
        if batch:
            self._reconcile_batch(batch, rejections, seen_keys, report)
        if two_way and since is not None:
            report.orphans = self._orphans(since, seen_keys)
        report.finished_at = datetime.now(UTC)
        return report

    def _rejections(self, since: datetime | None, run_id: str | None) -> dict[str, str]:
        """correlation ID -> 'CODE: reason' for everything on the dead-letter topic."""
        topic = self.mapping.source.rejected_topic
        if not topic:
            return {}
        rejections = {}
        for r in read_topic(self.settings.kafka_bootstrap_servers, topic, since, run_id):
            if r.correlation_id:
                code = r.headers.get("x-rejection-code", "UNKNOWN")
                rejections[r.correlation_id] = f"{code}: {r.headers.get('x-rejection-reason', '')}"
        return rejections

    def _parse(self, record: Record) -> ExpectedRecord | str:
        try:
            return self.extractor.extract(parse(record.value or b""))
        except (etree.XMLSyntaxError, ExtractionError) as exc:
            return str(exc)

    def _reconcile_batch(
        self,
        batch: list[Record],
        rejections: dict[str, str],
        seen_keys: set[str],
        report: ReconciliationReport,
    ) -> None:
        header = self.mapping.header
        parsed = [(record, self._parse(record)) for record in batch]
        keys = [p.key for _, p in parsed if isinstance(p, ExpectedRecord)]
        stored = self.oracle.fetch_by_key(header.table, header.key_column, keys)
        stored_children = {
            child.table: self.oracle.fetch_children(child.table, child.parent_key_column, keys)
            for child in self.mapping.children
        }

        for record, expected in parsed:
            if isinstance(expected, str):  # could not parse / no key
                cid = record.correlation_id
                if cid in rejections:
                    report.add(
                        MessageResult(
                            Status.REJECTED,
                            cid,
                            None,
                            record.partition,
                            record.offset,
                            detail=rejections[cid],
                        )
                    )
                else:
                    report.add(
                        MessageResult(
                            Status.UNPARSEABLE, cid, None, record.partition, record.offset, detail=expected
                        )
                    )
                continue

            cid = expected.correlation_id or record.correlation_id
            if expected.key in seen_keys:
                report.duplicate_messages += 1
            seen_keys.add(expected.key)

            row = stored.get(expected.key)
            if row is None:
                status, detail = (
                    (Status.REJECTED, rejections[cid]) if cid in rejections else (Status.MISSING, None)
                )
                report.add(
                    MessageResult(status, cid, expected.key, record.partition, record.offset, detail=detail)
                )
                continue

            diffs = list(expected.problems)
            diffs += compare_row(header.table, expected.header, row, header.columns)
            for child in self.mapping.children:
                item_spec = child.columns[child.item_key_column]
                actual_items = {
                    str(normalise_actual(r[child.item_key_column], item_spec)): r
                    for r in stored_children[child.table].get(expected.key, [])
                }
                diffs += compare_rows_by_key(
                    child.table, expected.children[child.table], actual_items, child.columns
                )
            status = Status.MISMATCHED if diffs else Status.MATCHED
            report.add(MessageResult(status, cid, expected.key, record.partition, record.offset, diffs))

    def _orphans(self, since: datetime, seen_keys: set[str]) -> list[str]:
        """Rows stored since `since` that no message on the topic accounts for."""
        header = self.mapping.header
        table, key, ts = (
            identifier(header.table),
            identifier(header.key_column),
            identifier(header.timestamp_column),
        )
        rows = self.oracle.query(
            f"SELECT {key} FROM {table} WHERE {ts} >= :since",
            {"since": since.astimezone(UTC).replace(tzinfo=None)},
        )
        return sorted({row[key] for row in rows} - seen_keys)
