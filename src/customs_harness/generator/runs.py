"""Publishing a test run and waiting for the pipeline to finish with it.

A *run* is one batch of generated messages tagged with an `x-run-id` header.
The services propagate headers, so every topic (and the reconciliation and
tracing tools) can scope to a single run. The manifest records what was sent,
when, and what outcome each message should have.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_hex

from customs_harness.config import Settings
from customs_harness.generator.scenarios import ACCEPTED, GeneratedMessage
from customs_harness.kafka import Publisher, read_topic
from customs_harness.oracle import OracleClient


def new_run_id() -> str:
    return f"run-{datetime.now(UTC):%Y%m%d-%H%M%S}-{token_hex(2)}"


@dataclass
class ManifestEntry:
    correlation_id: str
    declaration_ref: str
    scenario: str
    expected_outcome: str
    partition: int = -1
    offset: int = -1
    timestamp_ms: int = 0
    copies: int = 1


@dataclass
class RunManifest:
    run_id: str
    topic: str
    started_at: datetime
    entries: list[ManifestEntry] = field(default_factory=list)

    @property
    def correlation_ids(self) -> set[str]:
        return {e.correlation_id for e in self.entries}

    @property
    def expected_accepted(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.expected_outcome == ACCEPTED]

    @property
    def expected_rejected(self) -> list[ManifestEntry]:
        return [e for e in self.entries if e.expected_outcome != ACCEPTED]

    @property
    def read_from(self) -> datetime:
        """Start point for bounded topic reads (a little slack for clock rounding)."""
        return self.started_at - timedelta(seconds=2)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {**asdict(self), "started_at": self.started_at.isoformat()}
        path.write_text(json.dumps(data, indent=1))
        return path

    @classmethod
    def load(cls, path: Path) -> RunManifest:
        data = json.loads(path.read_text())
        return cls(
            run_id=data["run_id"],
            topic=data["topic"],
            started_at=datetime.fromisoformat(data["started_at"]),
            entries=[ManifestEntry(**e) for e in data["entries"]],
        )


def publish_run(
    settings: Settings,
    messages: list[GeneratedMessage],
    run_id: str | None = None,
    rate: float | None = None,
    duplicate_every: int = 0,
) -> RunManifest:
    """Publish messages to the inbound topic. `rate` caps messages/second;
    `duplicate_every=N` re-sends every Nth message (idempotency check)."""
    run_id = run_id or new_run_id()
    manifest = RunManifest(run_id=run_id, topic=settings.topic_inbound, started_at=datetime.now(UTC))
    publisher = Publisher(settings.kafka_bootstrap_servers, settings.topic_inbound)
    entries: dict[str, ManifestEntry] = {}
    started = time.monotonic()
    for index, message in enumerate(messages):
        if rate:
            time.sleep(max(0.0, started + index / rate - time.monotonic()))
        copies = 2 if duplicate_every and index % duplicate_every == duplicate_every - 1 else 1
        for _ in range(copies):
            publisher.publish(
                key=message.declaration_ref,
                value=message.payload,
                correlation_id=message.correlation_id,
                run_id=run_id,
                extra_headers={"x-scenario": message.scenario},
            )
        entries[message.correlation_id] = ManifestEntry(
            correlation_id=message.correlation_id,
            declaration_ref=message.declaration_ref,
            scenario=message.scenario,
            expected_outcome=message.expected_outcome,
            copies=copies,
        )
    for record in publisher.flush():
        entry = entries[record.correlation_id]
        if entry.timestamp_ms == 0 or record.timestamp_ms < entry.timestamp_ms:  # first copy wins
            entry.partition, entry.offset, entry.timestamp_ms = (
                record.partition,
                record.offset,
                record.timestamp_ms,
            )
    manifest.entries = list(entries.values())
    return manifest


@dataclass
class WaitResult:
    complete: bool
    persisted: set[str]
    rejected: set[str]
    waited_seconds: float

    @property
    def accounted(self) -> set[str]:
        return self.persisted | self.rejected


def accounted_for(
    settings: Settings, manifest: RunManifest, oracle: OracleClient
) -> tuple[set[str], set[str]]:
    """(correlation IDs stored in Oracle, correlation IDs on the dead-letter topic)."""
    ids = manifest.correlation_ids
    persisted = {row["CORRELATION_ID"] for row in oracle.fetch_in("DECLARATION", "CORRELATION_ID", ids)}
    rejected = {
        r.correlation_id
        for r in read_topic(
            settings.kafka_bootstrap_servers,
            settings.topic_rejected,
            since=manifest.read_from,
            run_id=manifest.run_id,
        )
    } & ids
    return persisted, rejected


def wait_for_run(
    settings: Settings, manifest: RunManifest, timeout: float = 120.0, stall_timeout: float = 25.0
) -> WaitResult:
    """Poll until every message is either stored or dead-lettered, progress stalls, or time runs out."""
    started = time.monotonic()
    last_count, last_change = -1, started
    with OracleClient.from_settings(settings) as oracle:
        while True:
            persisted, rejected = accounted_for(settings, manifest, oracle)
            count = len(persisted | rejected)
            now = time.monotonic()
            if count >= len(manifest.entries):
                return WaitResult(True, persisted, rejected, now - started)
            if count != last_count:
                last_count, last_change = count, now
            if now - last_change > stall_timeout or now - started > timeout:
                return WaitResult(False, persisted, rejected, now - started)
            time.sleep(1.0)
