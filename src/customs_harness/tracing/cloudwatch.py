"""CloudWatch Logs lookups by correlation ID.

Locally this queries the moto emulator; point AWS_ENDPOINT_URL at nothing
(empty) and it queries real CloudWatch with your normal AWS credentials.
The filter pattern - a quoted term - is the same one you would type into
the CloudWatch console.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3

from customs_harness.config import Settings


@dataclass(frozen=True)
class LogEvent:
    timestamp: datetime
    log_group: str
    service: str
    event: str
    fields: dict[str, Any]


def logs_client(settings: Settings) -> Any:
    if settings.aws_endpoint_url:  # local emulator: dummy credentials
        return boto3.client(
            "logs",
            endpoint_url=settings.aws_endpoint_url,
            region_name=settings.aws_default_region,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
    return boto3.client("logs", region_name=settings.aws_default_region)


def find_log_events(settings: Settings, correlation_id: str, since: datetime | None = None) -> list[LogEvent]:
    client = logs_client(settings)
    events: list[LogEvent] = []
    for group in filter(None, settings.cloudwatch_log_groups.split(",")):
        params: dict[str, Any] = {"logGroupName": group.strip(), "filterPattern": f'"{correlation_id}"'}
        if since is not None:
            params["startTime"] = int(since.timestamp() * 1000)
        try:
            for page in client.get_paginator("filter_log_events").paginate(**params):
                for raw in page["events"]:
                    try:
                        payload = json.loads(raw["message"])
                    except ValueError:
                        payload = {"event": raw["message"]}
                    events.append(
                        LogEvent(
                            timestamp=datetime.fromtimestamp(raw["timestamp"] / 1000, UTC),
                            log_group=group.strip(),
                            service=payload.get("service", group.rsplit("/", 1)[-1]),
                            event=payload.get("event", ""),
                            fields=payload,
                        )
                    )
        except client.exceptions.ResourceNotFoundException:
            continue
    return sorted(events, key=lambda e: e.timestamp)
