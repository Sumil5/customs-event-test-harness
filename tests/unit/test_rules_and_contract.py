"""Business rules: the harness's independent oracle, and contract checks against the services.

The contract tests import the services' pure rule functions (no Kafka or
Oracle needed) and prove that every generator scenario provokes exactly the
behaviour the scenario promises. If a developer changes a rule, these fail
in seconds rather than in an end-to-end run.
"""

import pytest

from customs_harness.generator.scenarios import SCENARIOS
from customs_harness.reconciliation.rules import expected_risk_score, lane_for_score
from customs_harness.xml import NSMAP, parse
from customs_services.assessment import lane_for, risk_score
from customs_services.common.faults import Faults
from customs_services.intake import business_rule_violation


@pytest.mark.parametrize(
    ("score", "lane"), [(0, "GREEN"), (39, "GREEN"), (40, "RED"), (41, "RED"), (100, "RED")]
)
def test_lane_threshold_boundary(score, lane):
    assert lane_for_score(score) == lane


def test_service_lane_threshold_agrees_at_the_boundary():
    assert [lane_for(s, Faults()) for s in (39, 40, 41)] == ["GREEN", "RED", "RED"]


def test_boundary_fault_changes_only_the_boundary():
    faulty = Faults.parse("lane_boundary")
    assert [lane_for(s, faulty) for s in (39, 40, 41)] == ["GREEN", "GREEN", "RED"]


@pytest.mark.parametrize("name", [n for n, s in SCENARIOS.items() if s.expected_outcome.startswith("R0")])
def test_each_rule_scenario_triggers_its_rejection_code(name, factory):
    for _ in range(10):
        message = SCENARIOS[name].generate(factory)
        violation = business_rule_violation(parse(message.payload))
        assert violation is not None and violation[0] == message.expected_outcome


@pytest.mark.parametrize("name", [n for n, s in SCENARIOS.items() if s.expected_outcome == "ACCEPTED"])
def test_valid_scenarios_pass_every_business_rule(name, factory):
    for _ in range(10):
        assert business_rule_violation(parse(SCENARIOS[name].generate(factory).payload)) is None


@pytest.mark.parametrize("name", [n for n, s in SCENARIOS.items() if s.expected_outcome == "ACCEPTED"])
def test_harness_oracle_and_service_agree_on_risk_score(name, factory):
    for _ in range(10):
        doc = parse(SCENARIOS[name].generate(factory).payload)
        assert expected_risk_score(doc, NSMAP) == risk_score(doc)


def test_rate_faults_are_deterministic_per_key():
    faults = Faults.parse("drop=0.5")
    keys = [f"26XI{n:014d}" for n in range(1000)]
    first = [faults.hits("drop", k) for k in keys]
    assert first == [faults.hits("drop", k) for k in keys]
    assert 400 < sum(first) < 600


def test_unknown_fault_names_are_refused():
    with pytest.raises(ValueError, match="unknown fault"):
        Faults.parse("rounding,make_it_slow")
