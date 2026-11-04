"""Structured JSON logging to stdout and, optionally, CloudWatch Logs.

Every line carries the service name and, where there is one, the message's
correlation ID. That is what makes a single declaration traceable across
services, whether you grep `docker compose logs` or query CloudWatch.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from customs_services.common.config import Settings


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger(settings.service_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = JsonFormatter(settings.service_name)

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(formatter)
    logger.addHandler(stdout)

    if settings.cloudwatch_enabled:
        try:
            import boto3
            import watchtower

            cloudwatch = watchtower.CloudWatchLogHandler(
                log_group_name=f"/customs/{settings.service_name}",
                log_stream_name=settings.instance_id,
                boto3_client=boto3.client("logs"),
                send_interval=1,
                create_log_group=True,
            )
            cloudwatch.setFormatter(formatter)
            logger.addHandler(cloudwatch)
        except Exception:  # never let log shipping stop the service
            logger.warning("cloudwatch.unavailable", exc_info=True)
    return logger


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": fields})
