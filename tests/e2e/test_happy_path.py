"""Valid traffic: every message is stored, and stored accurately."""

from collections import Counter

from customs_harness.generator.scenarios import VALID_MIX, generate
from customs_harness.reconciliation.engine import Status
from customs_harness.reconciliation.report import write_reports


def test_every_valid_scenario_is_stored_and_reconciles_field_by_field(run_pipeline, reconcile, settings):
    messages = [m for name in VALID_MIX for m in generate(10, {name: 1})]
    run = run_pipeline(messages=messages)

    assert run.wait.complete, f"only {len(run.wait.accounted)}/{len(messages)} messages accounted for"
    assert not run.wait.rejected, "valid messages were dead-lettered"

    report = reconcile(run)
    write_reports(report, settings.reports_dir / run.run_id)
    failing = Counter(run.scenario_of[r.correlation_id] for r in report.results)
    first = report.results[0].diffs[:3] if report.results else []
    assert report.passed, f"discrepancies by scenario: {dict(failing)}; e.g. {first}"
    assert report.count(Status.MATCHED) == len(messages)


def test_mixed_traffic_is_fully_accounted_for(run_pipeline, reconcile):
    run = run_pipeline(count=500)  # default mix, ~7% invalid by design
    report = reconcile(run)

    assert run.wait.complete
    assert report.messages_read == 500
    assert report.count(Status.MISSING) == 0
    assert report.count(Status.MATCHED) + report.count(Status.REJECTED) == 500
    assert report.count(Status.REJECTED) == len(run.manifest.expected_rejected)
    assert report.passed
