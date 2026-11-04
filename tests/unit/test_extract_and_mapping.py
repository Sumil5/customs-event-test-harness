"""Mapping file + XML extraction: XPath, typing, lookups and derived rules."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from customs_harness.generator.scenarios import SCENARIOS
from customs_harness.reconciliation.compare import UNMAPPED_CODE
from customs_harness.reconciliation.extract import ExtractionError, Extractor, coerce
from customs_harness.reconciliation.mapping import ColumnSpec, Mapping
from customs_harness.xml import NSMAP, parse


@pytest.fixture(scope="module")
def mapping(settings) -> Mapping:
    return Mapping.load(settings.mapping_path)


def test_mapping_file_loads_and_covers_both_tables(mapping):
    assert mapping.header.table == "DECLARATION"
    assert [c.table for c in mapping.children] == ["GOODS_ITEM"]
    assert {"RISK_SCORE", "LANE"} <= {c for c, s in mapping.header.columns.items() if s.derived}


def test_a_column_needs_exactly_one_source():
    with pytest.raises(ValidationError):
        ColumnSpec(xpath="a", derived="lane")
    with pytest.raises(ValidationError):
        ColumnSpec()


def test_extraction_produces_typed_expected_values(mapping, factory):
    message = SCENARIOS["standard"].generate(factory)
    d = message.declaration
    record = Extractor(mapping).extract(parse(message.payload))

    assert record.key == d.declaration_ref
    assert record.correlation_id == d.correlation_id
    h = record.header
    assert h["MESSAGE_CREATED_AT"] == d.created_at and h["MESSAGE_CREATED_AT"].tzinfo == UTC
    assert h["ARRIVAL_DATE"] == d.arrival_date
    assert h["TOTAL_INVOICE_VALUE"] == d.total_invoice_value
    assert h["UKIMS_AUTHORISED"] == ("Y" if d.ukims_authorised else "N")
    assert h["TRANSPORT_MODE_CODE"] == {"SEA": 1, "RAIL": 2, "ROAD": 3, "AIR": 4}[d.transport.mode]
    assert h["DECLARATION_CATEGORY"] == {"IMPORT": "IM", "EXPORT": "EX", "TRANSIT": "TR"}[d.category]
    assert h["GOODS_ITEM_COUNT"] == len(d.goods_items)
    assert h["LANE"] in {"GREEN", "RED"}

    items = record.children["GOODS_ITEM"]
    assert set(items) == {str(i.item_number) for i in d.goods_items}
    first = items["1"]
    assert first["NET_MASS_KG"] == d.goods_items[0].net_mass_kg
    assert first["DESCRIPTION"] == d.goods_items[0].description


def test_lexical_variants_extract_to_the_same_values(mapping, factory):
    message = SCENARIOS["lexical_variants"].generate(factory)
    record = Extractor(mapping).extract(parse(message.payload))
    assert record.header["UKIMS_AUTHORISED"] == ("Y" if message.declaration.ukims_authorised else "N")
    assert record.header["TOTAL_GROSS_MASS_KG"] == message.declaration.total_gross_mass_kg
    assert not record.problems


def test_optional_elements_extract_as_null(mapping, factory):
    record = Extractor(mapping).extract(parse(SCENARIOS["minimal"].generate(factory).payload))
    assert record.header["CONSIGNEE_EORI"] is None
    assert record.header["VEHICLE_REGISTRATION"] is None
    assert record.header["PORT_OF_ENTRY"] is None


def test_unmapped_code_is_reported_not_raised(mapping, factory):
    doc = parse(SCENARIOS["standard"].generate(factory).payload)
    doc.find("gm:Declaration/gm:Transport/gm:Mode", NSMAP).text = "HOVERCRAFT"
    record = Extractor(mapping).extract(doc)
    assert "TRANSPORT_MODE_CODE" not in record.header
    assert [(p.column, p.kind, p.expected) for p in record.problems] == [
        ("TRANSPORT_MODE_CODE", UNMAPPED_CODE, "HOVERCRAFT")
    ]


def test_message_without_business_key_cannot_be_reconciled(mapping, factory):
    doc = parse(SCENARIOS["invalid_missing_ref"].generate(factory).payload)
    with pytest.raises(ExtractionError):
        Extractor(mapping).extract(doc)


@pytest.mark.parametrize(
    ("raw", "spec", "expected"),
    [
        (" 12.500 ", ColumnSpec(xpath="x", type="decimal"), Decimal("12.5")),
        ("+3", ColumnSpec(xpath="x", type="integer"), 3),
        ("2026-11-20Z", ColumnSpec(xpath="x", type="date"), date(2026, 11, 20)),
        (
            "2026-07-01T13:00:00.250+01:00",
            ColumnSpec(xpath="x", type="datetime"),
            datetime(2026, 7, 1, 12, 0, 0, 250000, tzinfo=UTC),
        ),
        ("1", ColumnSpec(xpath="x", type="boolean", true_value="Y", false_value="N"), "Y"),
        ("false", ColumnSpec(xpath="x", type="boolean", true_value="Y", false_value="N"), "N"),
        ("  keep my spaces ", ColumnSpec(xpath="x"), "  keep my spaces "),
    ],
)
def test_coercion(raw, spec, expected):
    assert coerce(raw, spec) == expected


def test_invalid_boolean_is_rejected():
    with pytest.raises(ValueError):
        coerce("yes", ColumnSpec(xpath="x", type="boolean"))
