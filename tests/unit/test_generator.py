"""The payload generator: schema validity, determinism, and scenario intent."""

from decimal import Decimal

import pytest

from customs_harness.generator.factory import DeclarationFactory
from customs_harness.generator.scenarios import ACCEPTED, DEFAULT_MIX, SCENARIOS, VALID_MIX, generate
from customs_harness.reconciliation.rules import expected_lane, expected_risk_score
from customs_harness.xml import NSMAP, parse, schema_errors

SCHEMA_LEVEL = {"SCHEMA_INVALID", "MALFORMED_XML"}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_schema_validity_matches_the_expected_outcome(name, settings, factory):
    scenario = SCENARIOS[name]
    for _ in range(25):
        message = scenario.generate(factory)
        errors = schema_errors(message.payload, settings.schema_path)
        if scenario.expected_outcome in SCHEMA_LEVEL:
            assert errors, f"{name} should be schema-invalid"
        else:
            assert not errors, f"{name} should be schema-valid: {errors[:3]}"


def test_seed_reproduces_content_but_never_identifiers():
    first = generate(30, seed=99)
    second = generate(30, seed=99)
    assert [m.scenario for m in first] == [m.scenario for m in second]
    assert [m.declaration.goods_items[0].commodity_code for m in first] == [
        m.declaration.goods_items[0].commodity_code for m in second
    ]
    assert {m.declaration_ref for m in first}.isdisjoint({m.declaration_ref for m in second})
    assert {m.correlation_id for m in first}.isdisjoint({m.correlation_id for m in second})


def test_identifiers_are_unique_within_a_large_run():
    messages = generate(2000, seed=1)
    assert len({m.declaration_ref for m in messages}) == 2000
    assert len({m.correlation_id for m in messages}) == 2000


@pytest.mark.parametrize("name", list(VALID_MIX))
def test_valid_scenarios_keep_totals_consistent(name, factory):
    declaration = SCENARIOS[name].generate(factory).declaration
    assert declaration.total_invoice_value == sum(i.item_value for i in declaration.goods_items)
    assert declaration.total_gross_mass_kg >= sum(i.net_mass_kg for i in declaration.goods_items)


@pytest.mark.parametrize(
    ("name", "score", "lane"),
    [("green_lane", 10, "GREEN"), ("lane_boundary", 40, "RED")],
)
def test_lane_scenarios_hit_their_intended_risk_score(name, score, lane, factory):
    for _ in range(20):
        doc = parse(SCENARIOS[name].generate(factory).payload)
        assert expected_risk_score(doc, NSMAP) == score
        assert expected_lane(doc, NSMAP) == lane


def test_red_lane_scenario_is_always_red(factory):
    for _ in range(20):
        doc = parse(SCENARIOS["red_lane"].generate(factory).payload)
        assert expected_risk_score(doc, NSMAP) >= 45
        assert expected_lane(doc, NSMAP) == "RED"


def test_max_length_scenario_sits_exactly_on_the_limits(factory):
    declaration = SCENARIOS["max_length_text"].generate(factory).declaration
    assert len(declaration.consignor.name) == 70
    assert len(declaration.goods_items[0].description) == 512


def test_unicode_survives_utf8_serialisation(factory):
    message = SCENARIOS["unicode_text"].generate(factory)
    doc = parse(message.payload)
    assert (
        doc.findtext("gm:Declaration/gm:Consignor/gm:Name", namespaces=NSMAP)
        == message.declaration.consignor.name
    )
    assert any(ord(ch) > 127 for ch in message.declaration.consignor.name)


def test_lexical_variants_use_unusual_but_legal_forms(factory):
    doc = parse(SCENARIOS["lexical_variants"].generate(factory).payload)
    assert doc.findtext("gm:Declaration/gm:UkimsAuthorised", namespaces=NSMAP) in {"0", "1"}
    gross = doc.findtext("gm:Declaration/gm:TotalGrossMassKg", namespaces=NSMAP)
    assert gross != gross.strip() and gross.strip().startswith("+")
    assert Decimal(gross.strip()) > 0


def test_default_mix_is_mostly_valid():
    invalid_weight = sum(w for name, w in DEFAULT_MIX.items() if SCENARIOS[name].expected_outcome != ACCEPTED)
    assert 0 < invalid_weight / sum(DEFAULT_MIX.values()) < 0.10


def test_unknown_scenario_in_mix_is_rejected():
    with pytest.raises(ValueError, match="unknown scenarios"):
        generate(1, {"no_such_scenario": 1})


def test_factory_with_fixed_clock_dates_messages_consistently():
    from datetime import UTC, datetime

    now = datetime(2026, 7, 1, 12, 0, 0, 123456, tzinfo=UTC)
    declaration = DeclarationFactory(seed=5, now=now).declaration()
    assert declaration.created_at == now.replace(microsecond=123000)
    assert declaration.declaration_ref.startswith("26")
    assert 0 <= (declaration.arrival_date - now.date()).days <= 14
