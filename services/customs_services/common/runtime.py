"""Consume -> transform -> produce loop shared by every service.

Delivery is at-least-once: a batch's output is flushed to Kafka (and, for the
persistence service, committed to Oracle) *before* the consumer offsets are
committed. A crash mid-batch means the batch is replayed, so downstream
consumers must be idempotent - which the persistence service is.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from confluent_kafka import Consumer, KafkaError, Message, Producer

from customs_services.common.config import Settings
from customs_services.common.heartbeat import Heartbeat
from customs_services.common.logs import log_event

CORRELATION_HEADER = "correlation_id"


@dataclass(frozen=True)
class Inbound:
    topic: str
    partition: int
    offset: int
    timestamp_ms: int
    key: bytes | None
    value: bytes
    headers: dict[str, str]

    @property
    def correlation_id(self) -> str:
        return self.headers.get(CORRELATION_HEADER, "unknown")

    @classmethod
    def from_kafka(cls, msg: Message) -> Inbound:
        headers = {
            name: value.decode("utf-8", "replace") if value is not None else ""
            for name, value in (msg.headers() or [])
        }
        _, timestamp_ms = msg.timestamp()
        return cls(
            topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset(),
            timestamp_ms=timestamp_ms,
            key=msg.key(),
            value=msg.value() or b"",
            headers=headers,
        )


@dataclass(frozen=True)
class Outbound:
    topic: str
    key: bytes | None
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)


BatchHandler = Callable[[list[Inbound]], list[Outbound]]


def forward(msg: Inbound, topic: str, value: bytes | None = None, **extra_headers: str) -> Outbound:
    """Pass a message on to the next topic, propagating its headers."""
    return Outbound(topic, msg.key, msg.value if value is None else value, {**msg.headers, **extra_headers})


def reject(settings: Settings, msg: Inbound, code: str, reason: str) -> Outbound:
    """Dead-letter a message: the original payload plus rejection metadata in headers."""
    return forward(
        msg,
        settings.topics.rejected,
        **{
            "x-rejection-code": code,
            "x-rejection-reason": reason[:500],
            "x-rejected-by": settings.service_name,
            "x-source-topic": msg.topic,
        },
    )


class ServiceRunner:
    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
        source_topic: str,
        handler: BatchHandler,
        batch_size: int = 500,
        poll_timeout: float = 0.05,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self.source_topic = source_topic
        self.handler = handler
        self.batch_size = batch_size
        self.poll_timeout = poll_timeout
        self._stop = threading.Event()
        self._delivery_errors: list[KafkaError] = []

    def _on_delivery(self, err: KafkaError | None, _msg: Message) -> None:
        if err is not None:
            self._delivery_errors.append(err)

    def stop(self, *_args: object) -> None:
        self._stop.set()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        name = self.settings.service_name
        consumer = Consumer(
            {
                "bootstrap.servers": self.settings.bootstrap_servers,
                "group.id": f"customs-{name}",
                "client.id": f"{name}-{self.settings.instance_id}",
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
        producer = Producer(
            {
                "bootstrap.servers": self.settings.bootstrap_servers,
                "client.id": f"{name}-{self.settings.instance_id}",
                "enable.idempotence": True,
                "linger.ms": 2,
            }
        )
        heartbeat = Heartbeat()
        consumer.subscribe([self.source_topic])
        log_event(self.logger, "service.started", topic=self.source_topic, faults=self.settings.faults)

        try:
            while not self._stop.is_set():
                if consumer.assignment():
                    heartbeat.beat()
                messages = consumer.consume(num_messages=self.batch_size, timeout=self.poll_timeout)
                if not messages:
                    continue
                batch = []
                for message in messages:
                    if message.error():
                        log_event(self.logger, "kafka.error", logging.ERROR, error=str(message.error()))
                        continue
                    batch.append(Inbound.from_kafka(message))

                for out in self.handler(batch):
                    producer.produce(
                        out.topic,
                        key=out.key,
                        value=out.value,
                        headers=list(out.headers.items()),
                        on_delivery=self._on_delivery,
                    )
                    producer.poll(0)
                if producer.flush(30) or self._delivery_errors:
                    raise RuntimeError(f"delivery failed: {self._delivery_errors or 'flush timeout'}")
                consumer.commit(asynchronous=False)
        except Exception:
            # Offsets for the failed batch were not committed: after the container
            # restarts, the batch is replayed (at-least-once).
            self.logger.exception("service.crashed")
            raise
        finally:
            log_event(self.logger, "service.stopping")
            consumer.close()
            producer.flush(10)
            logging.shutdown()
