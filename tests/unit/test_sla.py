"""Trace maths: percentiles, outcomes, breaches, stalls and bottlenecks."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from customs_harness.tracing.collector import Trace
from customs_harness.tracing.sla import LatencyStats, SlaReport, percentile

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=UTC)


def make_trace(cid: str, *offsets_ms: float, rejected: bool = False) -> Trace:
    stages = ["submitted", "validated", "assessed", "persisted"]
    trace = Trace(cid)
    for stage, offset in zip(stages, offsets_ms, strict=False):
        trace.events[stage] = T0 + timedelta(milliseconds=offset)
    if rejected:
        trace.events["rejected"] = T0 + timedelta(milliseconds=offsets_ms[-1] + 5)
    return trace


@pytest.mark.parametrize(("pct", "expected"), [(0, 1), (50, 3), (90, 4.6), (100, 5)])
def test_percentile_interpolates_like_numpy(pct, expected):
    assert percentile([1, 2, 3, 4, 5], pct) == pytest.approx(expected)


def test_percentile_of_nothing_is_nan():
    assert math.isnan(percentile([], 95))
    assert LatencyStats.of([]).count == 0


def test_trace_latencies():
    trace = make_trace("a", 0, 40, 100, 160)
    assert trace.outcome == "PERSISTED"
    assert trace.end_to_end_ms == pytest.approx(160)
    assert trace.hop_ms == pytest.approx({"intake": 40, "assessment": 60, "persistence": 60})
    assert trace.slowest_hop in {"assessment", "persistence"}


def test_rejected_trace_ends_at_the_dead_letter_topic():
    trace = make_trace("r", 0, rejected=True)
    assert trace.outcome == "REJECTED"
    assert trace.end_to_end_ms == pytest.approx(5)


def test_incomplete_trace_reports_where_it_stopped():
    trace = make_trace("lost", 0, 30, 70)
    assert trace.outcome == "INCOMPLETE"
    assert trace.end_to_end_ms is None
    assert trace.last_stage == "assessed"


def test_report_finds_breaches_stalls_and_the_bottleneck():
    traces = [make_trace(f"ok{i}", 0, 20, 50, 90) for i in range(20)]
    # three messages held up in assessment
    traces += [make_trace(f"slow{i}", 0, 20, 14_020 + i, 14_060 + i) for i in range(3)]
    traces.append(make_trace("lost", 0, 20, 50))
    report = SlaReport(sla_seconds=13, traces=traces)

    assert [t.correlation_id for t in report.breaches] == ["slow2", "slow1", "slow0"]  # slowest first
    assert report.breach_causes == {"assessment": 3}
    assert report.stalled_after == {"assessed": 1}
    assert report.bottleneck == "assessment"  # highest p95
    assert report.end_to_end.max == pytest.approx(14_062)
    assert not report.passed


def test_one_slow_message_breaches_without_moving_the_p95():
    traces = [make_trace(f"ok{i}", 0, 20, 50, 90) for i in range(40)]
    traces.append(make_trace("slow", 0, 20, 14_020, 14_060))
    report = SlaReport(sla_seconds=13, traces=traces)
    assert len(report.breaches) == 1  # an SLA is about every message, not the percentile
    assert report.end_to_end.p95 < 1000


def test_clean_run_passes_and_histogram_counts_everything():
    traces = [make_trace(f"t{i}", 0, 10, 20, 30 + i * 10) for i in range(50)]
    report = SlaReport(sla_seconds=13, traces=traces)
    assert report.passed
    assert sum(n for _, n in report.histogram) == 50


def test_anomalies_fail_the_report():
    trace = make_trace("x", 0, 10, 20, 30)
    trace.anomalies.append("row committed before the message was sent")
    assert not SlaReport(sla_seconds=13, traces=[trace]).passed


def test_empty_report_does_not_pass():
    assert not SlaReport(sla_seconds=13, traces=[]).passed
