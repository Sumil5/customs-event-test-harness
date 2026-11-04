"""Timeliness: every message reaches its final state within the SLA (13 s)."""

import pytest
from confluent_kafka import TIMESTAMP_LOG_APPEND_TIME

from customs_harness.kafka import read_topic
from customs_harness.tracing.collector import STAGES, collect_traces
from customs_harness.tracing.report import write_reports
from customs_harness.tracing.sla import SlaReport


@pytest.fixture(scope="module")
def traced_run(run_pipeline, settings, oracle):
    run = run_pipeline(count=500, rate=100)
    traces = collect_traces(
        settings,
        oracle,
        since=run.manifest.read_from,
        run_id=run.run_id,
        correlation_ids=run.manifest.correlation_ids,
    )
    report = SlaReport(settings.sla_seconds, list(traces.values()), run.run_id)
    write_reports(report, settings.reports_dir / run.run_id)
    return run, report


def test_every_message_completes_within_the_sla(traced_run, settings):
    _, report = traced_run
    assert not report.incomplete, f"{len(report.incomplete)} incomplete: {dict(report.stalled_after)}"
    if report.breaches:
        worst = report.breaches[0]
        pytest.fail(
            f"{len(report.breaches)} SLA breaches; worst {worst.end_to_end_ms:.0f}ms "
            f"({worst.correlation_id}), slowest stage {worst.slowest_hop}"
        )
    assert report.end_to_end.max <= settings.sla_seconds * 1000
    assert report.passed


def test_journeys_move_forward_through_every_stage(traced_run):
    _, report = traced_run
    persisted = [t for t in report.traces if t.outcome == "PERSISTED"]
    assert persisted
    for trace in persisted:
        times = [trace.events[stage] for stage in STAGES]
        assert times == sorted(times), f"{trace.correlation_id} went backwards: {trace.events}"


def test_rejections_are_traced_to_the_dead_letter_topic(traced_run):
    run, report = traced_run
    rejected = {t.correlation_id for t in report.traces if t.outcome == "REJECTED"}
    assert rejected == {e.correlation_id for e in run.manifest.expected_rejected}


@pytest.mark.parametrize("stage", ["submitted", "validated", "assessed", "rejected"])
def test_stage_timestamps_come_from_the_broker_clock(stage, traced_run, settings):
    """With CreateTime, client start-up delays and client clock skew would be measured as latency."""
    run, _ = traced_run
    topic = settings.stage_topics[stage]
    record = next(
        read_topic(settings.kafka_bootstrap_servers, topic, since=run.manifest.read_from, run_id=run.run_id),
        None,
    )
    assert record is not None, f"no records for this run on {topic}"
    assert record.timestamp_type == TIMESTAMP_LOG_APPEND_TIME, f"{topic} is not using LogAppendTime"
