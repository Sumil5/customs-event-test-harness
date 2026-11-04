"""Kafka producer wrapper that records exactly when, and where, each message landed."""

from __future__ import annotations

import time
from dataclasses import dataclass

from confluent_kafka import KafkaError, Message, Producer

from customs_harness.kafka.reader import CORRELATION_HEADER, RUN_ID_HEADER


@dataclass(frozen=True)
class PublishedRecord:
    correlation_id: str
    key: str
    topic: str
    partition: int
    offset: int
    timestamp_ms: int


class Publisher:
    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        self.topic = topic
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "enable.idempotence": True,
                "linger.ms": 5,
                "compression.type": "zstd",
            }
        )
        # Connect and fetch metadata *before* the first message is time-stamped. A cold
        # producer needs ~0.5 s before it can send; without this, the first messages of
        # every run would have that start-up time counted as pipeline latency.
        self._producer.list_topics(topic, timeout=10)
        self._delivered: list[PublishedRecord] = []
        self._errors: list[str] = []

    def publish(
        self,
        key: str,
        value: bytes,
        correlation_id: str,
        run_id: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        headers = {
            CORRELATION_HEADER: correlation_id,
            RUN_ID_HEADER: run_id,
            "message_type": "GoodsMovementDeclaration",
            **(extra_headers or {}),
        }

        def on_delivery(err: KafkaError | None, msg: Message) -> None:
            if err is not None:
                self._errors.append(f"{correlation_id}: {err}")
                return
            self._delivered.append(
                PublishedRecord(
                    correlation_id, key, msg.topic(), msg.partition(), msg.offset(), msg.timestamp()[1]
                )
            )

        while True:
            try:
                self._producer.produce(
                    self.topic,
                    key=key.encode(),
                    value=value,
                    headers=list(headers.items()),
                    on_delivery=on_delivery,
                )
                break
            except BufferError:  # local queue full: let it drain
                self._producer.poll(0.1)
        self._producer.poll(0)

    def flush(self, timeout: float = 60.0) -> list[PublishedRecord]:
        deadline = time.monotonic() + timeout
        while self._producer.flush(1.0) and time.monotonic() < deadline:
            pass
        if self._errors:
            raise RuntimeError(f"{len(self._errors)} messages failed to publish, e.g. {self._errors[:3]}")
        delivered, self._delivered = self._delivered, []
        return delivered
