"""Bounded topic reads: everything from a start time up to the end of the topic *now*.

Reconciliation and tracing both need a finite, repeatable slice of a topic
rather than an endless subscription. The reader snapshots each partition's
high watermark, seeks to the first offset at or after `since`, and stops
once every partition reaches its snapshot. It never commits offsets, so it
never disturbs the services' consumer groups.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from confluent_kafka import OFFSET_END, Consumer, TopicPartition

CORRELATION_HEADER = "correlation_id"
RUN_ID_HEADER = "x-run-id"


@dataclass(frozen=True)
class Record:
    topic: str
    partition: int
    offset: int
    timestamp_ms: int
    key: bytes | None
    value: bytes | None
    headers: dict[str, str]
    timestamp_type: int = 0  # confluent_kafka.TIMESTAMP_LOG_APPEND_TIME when the broker set it

    @property
    def correlation_id(self) -> str | None:
        return self.headers.get(CORRELATION_HEADER)

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp_ms / 1000, UTC)


def read_topic(
    bootstrap_servers: str,
    topic: str,
    since: datetime | None = None,
    run_id: str | None = None,
    idle_timeout: float = 30.0,
) -> Iterator[Record]:
    """Yield records from `topic` published at or after `since`, optionally only those
    tagged with header x-run-id == run_id."""
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": f"harness-reader-{uuid.uuid4()}",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    try:
        metadata = consumer.list_topics(topic, timeout=10)
        if metadata.topics[topic].error is not None:
            raise RuntimeError(f"topic {topic}: {metadata.topics[topic].error}")
        partitions = sorted(metadata.topics[topic].partitions)

        end_offsets = {
            p: consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)[1] for p in partitions
        }
        if since is not None:
            wanted = [TopicPartition(topic, p, int(since.timestamp() * 1000)) for p in partitions]
            start_offsets = {
                tp.partition: (end_offsets[tp.partition] if tp.offset in (-1, OFFSET_END) else tp.offset)
                for tp in consumer.offsets_for_times(wanted, timeout=10)
            }
        else:
            start_offsets = {
                p: consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)[0] for p in partitions
            }

        remaining = {p for p in partitions if start_offsets[p] < end_offsets[p]}
        if not remaining:
            return
        consumer.assign([TopicPartition(topic, p, start_offsets[p]) for p in remaining])

        last_progress = time.monotonic()
        while remaining:
            messages = consumer.consume(num_messages=1000, timeout=1.0)
            if not messages:
                # Offsets can have gaps (transaction markers, compaction), so also check positions.
                for tp in consumer.position([TopicPartition(topic, p) for p in remaining]):
                    if tp.offset >= end_offsets[tp.partition]:
                        remaining.discard(tp.partition)
                if remaining and time.monotonic() - last_progress > idle_timeout:
                    raise TimeoutError(f"stalled reading {topic}; partitions left: {sorted(remaining)}")
                continue
            last_progress = time.monotonic()
            for message in messages:
                if message.error():
                    raise RuntimeError(f"kafka error reading {topic}: {message.error()}")
                partition, offset = message.partition(), message.offset()
                if partition not in remaining:
                    continue
                if offset >= end_offsets[partition] - 1:
                    remaining.discard(partition)
                if offset >= end_offsets[partition]:
                    continue
                headers = {
                    name: value.decode("utf-8", "replace") if value is not None else ""
                    for name, value in (message.headers() or [])
                }
                if run_id is not None and headers.get(RUN_ID_HEADER) != run_id:
                    continue
                timestamp_type, timestamp_ms = message.timestamp()
                yield Record(
                    topic=topic,
                    partition=partition,
                    offset=offset,
                    timestamp_ms=timestamp_ms,
                    key=message.key(),
                    value=message.value(),
                    headers=headers,
                    timestamp_type=timestamp_type,
                )
    finally:
        consumer.close()
