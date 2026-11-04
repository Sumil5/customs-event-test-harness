"""Rebuilds each message's journey through the pipeline from its correlation ID.

Every service propagates the `correlation_id` Kafka header, so the record
timestamps on each topic mark when a message cleared each stage:

    submitted (inbound) -> validated -> assessed -> persisted (Oracle commit time)
                         \\-> rejected (dead-letter topic)

The gaps between them are the per-service latencies; the whole span is the
end-to-end time the SLA applies to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from customs_harness.config import Settings
from customs_harness.kafka import read_topic
from customs_harness.oracle import OracleClient
from customs_harness.oracle.client import identifier

STAGES = ("submitted", "validated", "assessed", "persisted")
# Containers share one clock locally; across real hosts allow for some skew.
CLOCK_TOLERANCE = timedelta(milliseconds=50)

HOPS = (
    ("intake", "submitted", "validated"),
    ("assessment", "validated", "assessed"),
    ("persistence", "assessed", "persisted"),
)


@dataclass
class Trace:
    correlation_id: str
    declaration_ref: str | None = None
    scenario: str | None = None
    rejection_code: str | None = None
    events: dict[str, datetime] = field(default_factory=dict)  # stage -> first time seen
    anomalies: list[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if "persisted" in self.events:
            return "PERSISTED"
        if "rejected" in self.events:
            return "REJECTED"
        return "INCOMPLETE"

    @property
    def finished_at(self) -> datetime | None:
        return self.events.get("persisted") or self.events.get("rejected")

    @property
    def end_to_end_ms(self) -> float | None:
        start, end = self.events.get("submitted"), self.finished_at
        if start is None or end is None:
            return None
        return (end - start).total_seconds() * 1000

    @property
    def hop_ms(self) -> dict[str, float]:
        return {
            hop: (self.events[end] - self.events[start]).total_seconds() * 1000
            for hop, start, end in HOPS
            if start in self.events and end in self.events
        }

    @property
    def last_stage(self) -> str:
        reached = [stage for stage in STAGES if stage in self.events]
        return reached[-1] if reached else "not submitted"

    @property
    def slowest_hop(self) -> str | None:
        hops = self.hop_ms
        return max(hops, key=hops.get) if hops else None


def collect_traces(
    settings: Settings,
    oracle: OracleClient,
    since: datetime | None,
    run_id: str | None = None,
    correlation_ids: set[str] | None = None,
) -> dict[str, Trace]:
    """Traces keyed by correlation ID. With `correlation_ids`, messages that never
    appear anywhere still get an (empty, INCOMPLETE) trace."""
    traces: dict[str, Trace] = {cid: Trace(cid) for cid in correlation_ids or ()}

    for stage, topic in settings.stage_topics.items():
        for record in read_topic(settings.kafka_bootstrap_servers, topic, since=since, run_id=run_id):
            cid = record.correlation_id
            if not cid or (correlation_ids is not None and cid not in correlation_ids):
                continue
            trace = traces.setdefault(cid, Trace(cid))
            trace.events.setdefault(stage, record.timestamp)  # duplicates: first sighting wins
            if record.key and trace.declaration_ref is None:
                trace.declaration_ref = record.key.decode("utf-8", "replace")
            if stage == "submitted":
                trace.scenario = record.headers.get("x-scenario")
            elif stage == "rejected":
                trace.rejection_code = record.headers.get("x-rejection-code")

    table = identifier(settings.trace_table)
    column = identifier(settings.trace_correlation_column)
    timestamp = identifier(settings.trace_timestamp_column)
    for row in oracle.fetch_in(table, column, list(traces)):
        stored_at = row[timestamp]
        stored_at = stored_at.replace(tzinfo=UTC) if stored_at.tzinfo is None else stored_at.astimezone(UTC)
        trace = traces[row[column]]
        submitted = trace.events.get("submitted")
        if submitted is not None and stored_at < submitted - CLOCK_TOLERANCE:
            # The row pre-dates this message: a key collision or replay, not this message's write.
            trace.anomalies.append(f"row committed {stored_at:%H:%M:%S.%f} before the message was sent")
            continue
        trace.events.setdefault("persisted", stored_at)
    return traces
