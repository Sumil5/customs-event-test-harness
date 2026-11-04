"""Observability: each service logs every message, by correlation ID, to CloudWatch Logs."""

import os
import time

import pytest

from customs_harness.tracing.cloudwatch import find_log_events


@pytest.mark.skipif(
    os.environ.get("CLOUDWATCH_ENABLED", "true").lower() == "false",
    reason="the stack was started with CLOUDWATCH_ENABLED=false",
)
def test_every_service_logs_the_message_by_correlation_id(run_pipeline, settings):
    run = run_pipeline(count=3, mix={"standard": 1})
    correlation_id = run.manifest.entries[0].correlation_id

    deadline, steps = time.monotonic() + 20, []
    while time.monotonic() < deadline:  # log shipping is asynchronous (batched every second)
        steps = [
            (e.service, e.event) for e in find_log_events(settings, correlation_id, run.manifest.read_from)
        ]
        if ("persistence", "declaration.persisted") in steps:
            break
        time.sleep(1)

    assert steps == [
        ("intake", "declaration.accepted"),
        ("assessment", "declaration.assessed"),
        ("persistence", "declaration.persisted"),
    ]
