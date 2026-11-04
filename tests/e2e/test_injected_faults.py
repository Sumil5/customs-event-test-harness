"""Mutation testing for the harness: switch on known defects and prove each one is caught.

Runs only when the stack was started with faults, e.g.

    make faults-on && make test-faults

Fault selection in the services is a deterministic hash of the declaration
reference, so this test can *predict* exactly which messages each fault
will hit, and assert the reconciliation and SLA reports name those messages
- not just "something failed".
"""

from __future__ import annotations

import os
from collections import defaultdict

import pytest

from customs_harness.generator.scenarios import ACCEPTED, generate
from customs_harness.reconciliation.engine import Status
from customs_harness.reconciliation.report import write_reports as write_recon
from customs_harness.reconciliation.rules import expected_risk_score
from customs_harness.tracing.collector import collect_traces
from customs_harness.tracing.report import write_reports as write_sla
from customs_harness.tracing.sla import SlaReport
from customs_harness.xml import NSMAP, parse
from customs_services.common.faults import Faults

PERSISTENCE = Faults.parse(os.environ.get("PERSISTENCE_FAULTS", ""))
ASSESSMENT = Faults.parse(os.environ.get("ASSESSMENT_FAULTS", ""))

pytestmark = pytest.mark.skipif(
    not (PERSISTENCE or ASSESSMENT),
    reason="stack is running without faults (start it with `make faults-on`)",
)


def _plan_messages(count: int = 600):
    """Generate traffic containing exactly one message the delay fault will hold up."""
    messages, delayed = [], []
    while len(messages) < count or (ASSESSMENT.enabled("delay") and not delayed):
        for message in generate(100):
            hit = message.expected_outcome == ACCEPTED and ASSESSMENT.hits("delay", message.declaration_ref)
            if hit and delayed:
                continue  # one 14s stall is enough
            if hit:
                delayed.append(message)
            messages.append(message)
    if delayed:  # put it near the front so traffic queues up behind it
        messages.remove(delayed[0])
        messages.insert(20, delayed[0])
    return messages, {m.correlation_id for m in delayed}


@pytest.fixture(scope="module")
def faulty_run(run_pipeline, reconcile, settings, oracle):
    messages, delayed = _plan_messages()
    run = run_pipeline(messages=messages, rate=200)
    recon = reconcile(run)
    traces = collect_traces(
        settings,
        oracle,
        since=run.manifest.read_from,
        run_id=run.run_id,
        correlation_ids=run.manifest.correlation_ids,
    )
    sla = SlaReport(settings.sla_seconds, list(traces.values()), run.run_id)
    write_recon(recon, settings.reports_dir / run.run_id)
    write_sla(sla, settings.reports_dir / run.run_id)
    return run, recon, sla, delayed


@pytest.fixture(scope="module")
def accepted(faulty_run):
    run, *_ = faulty_run
    return [m for m in run.messages if m.expected_outcome == ACCEPTED]


@pytest.fixture(scope="module")
def stored(accepted):
    return [m for m in accepted if not PERSISTENCE.hits("drop", m.declaration_ref)]


@pytest.fixture(scope="module")
def diffs_by_ref(faulty_run):
    _, recon, *_ = faulty_run
    grouped = defaultdict(dict)
    for result in recon.results:
        for diff in result.diffs:
            grouped[result.key][diff.path] = diff
    return grouped


def test_the_run_fails(faulty_run):
    _, recon, sla, _ = faulty_run
    assert not recon.passed
    assert not sla.passed


@pytest.mark.skipif(not PERSISTENCE.enabled("drop"), reason="drop fault off")
def test_exactly_the_dropped_messages_are_missing(faulty_run, accepted):
    _, recon, sla, _ = faulty_run
    predicted = {m.declaration_ref for m in accepted if PERSISTENCE.hits("drop", m.declaration_ref)}
    assert predicted, "no message selected by the drop fault"
    assert {r.key for r in recon.results if r.status is Status.MISSING} == predicted
    assert {t.declaration_ref for t in sla.incomplete} == predicted
    assert set(sla.stalled_after) == {"assessed"}  # isolates persistence as the culprit


@pytest.mark.skipif(not PERSISTENCE.enabled("rounding"), reason="rounding fault off")
def test_rounding_is_caught_wherever_it_changes_the_value(stored, diffs_by_ref):
    affected = [
        m for m in stored if m.declaration.total_gross_mass_kg != round(m.declaration.total_gross_mass_kg, 2)
    ]
    assert affected
    for message in affected:
        diff = diffs_by_ref[message.declaration_ref]["DECLARATION.TOTAL_GROSS_MASS_KG"]
        assert diff.hint == "rounded to 2 dp"


@pytest.mark.skipif(not PERSISTENCE.enabled("truncate_description"), reason="truncate fault off")
def test_long_descriptions_are_reported_truncated(stored, diffs_by_ref):
    affected = [m for m in stored if any(len(i.description) > 100 for i in m.declaration.goods_items)]
    assert affected
    for message in affected:
        hints = {
            d.hint
            for path, d in diffs_by_ref[message.declaration_ref].items()
            if path.endswith("DESCRIPTION")
        }
        assert "truncated to 100 chars" in hints


@pytest.mark.skipif(not PERSISTENCE.enabled("ascii_names"), reason="ascii fault off")
def test_lost_accents_are_reported(stored, diffs_by_ref):
    affected = [m for m in stored if not m.declaration.consignor.name.isascii()]
    assert affected
    for message in affected:
        assert (
            diffs_by_ref[message.declaration_ref]["DECLARATION.CONSIGNOR_NAME"].hint
            == "non-ASCII characters lost"
        )


@pytest.mark.skipif(not PERSISTENCE.enabled("skip_last_item"), reason="skip_last_item fault off")
def test_lost_goods_items_are_reported(stored, diffs_by_ref):
    multi = [m for m in stored if len(m.declaration.goods_items) > 1]
    assert multi
    for message in multi:
        last = message.declaration.goods_items[-1].item_number
        assert diffs_by_ref[message.declaration_ref][f"GOODS_ITEM[{last}].*"].kind == "MISSING_ROW"


@pytest.mark.skipif(not ASSESSMENT.enabled("lane_boundary"), reason="lane fault off")
def test_lane_mismatches_are_exactly_the_boundary_cases(faulty_run, stored, diffs_by_ref):
    boundary = {m.declaration_ref for m in stored if expected_risk_score(parse(m.payload), NSMAP) == 40}
    flagged = {ref for ref, diffs in diffs_by_ref.items() if "DECLARATION.LANE" in diffs}
    assert boundary and flagged == boundary


@pytest.mark.skipif(not ASSESSMENT.enabled("delay"), reason="delay fault off")
def test_the_stall_breaches_the_sla_and_points_at_assessment(faulty_run):
    _, _, sla, delayed = faulty_run
    breached = {t.correlation_id for t in sla.breaches}
    assert delayed <= breached
    assert sla.breach_causes.most_common(1)[0][0] == "assessment"
    assert sla.end_to_end.max >= ASSESSMENT.delay_ms() > sla.sla_ms


def test_fault_free_columns_still_match(faulty_run, diffs_by_ref):
    """Faults must not create noise: unaffected columns never show up."""
    affected_columns = set()
    if PERSISTENCE.enabled("rounding"):
        affected_columns.add("DECLARATION.TOTAL_GROSS_MASS_KG")
    if PERSISTENCE.enabled("ascii_names"):
        affected_columns |= {"DECLARATION.CONSIGNOR_NAME", "DECLARATION.CONSIGNEE_NAME"}
    if PERSISTENCE.enabled("drop_millis"):
        affected_columns.add("DECLARATION.MESSAGE_CREATED_AT")
    if ASSESSMENT.enabled("lane_boundary"):
        affected_columns.add("DECLARATION.LANE")
    seen = {path for diffs in diffs_by_ref.values() for path in diffs if not path.startswith("GOODS_ITEM")}
    assert seen <= affected_columns
