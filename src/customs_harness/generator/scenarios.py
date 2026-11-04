"""Scenario catalogue: named recipes for test messages, each with an expected outcome.

A scenario builds a declaration with the factory and may then mutate the
rendered XML (to make it invalid, or to use unusual-but-legal lexical forms).
Expected outcomes let tests assert that valid messages are stored and invalid
ones are rejected with the right reason code.

    harness scenarios          # list them
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from lxml import etree

from customs_harness.generator import reference_data as ref
from customs_harness.generator.factory import DeclarationFactory
from customs_harness.generator.model import Declaration
from customs_harness.generator.render import to_bytes, to_element
from customs_harness.xml import NSMAP

ACCEPTED = "ACCEPTED"


@dataclass(frozen=True)
class GeneratedMessage:
    scenario: str
    expected_outcome: str  # ACCEPTED or a rejection code, e.g. R002_INVOICE_TOTAL
    correlation_id: str
    declaration_ref: str
    payload: bytes
    declaration: Declaration


Builder = Callable[[DeclarationFactory], Declaration]
XmlMutator = Callable[[etree._Element, random.Random], None]


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    build: Builder
    expected_outcome: str = ACCEPTED
    mutate_xml: XmlMutator | None = None
    corrupt_bytes: bool = False

    def generate(self, factory: DeclarationFactory) -> GeneratedMessage:
        declaration = self.build(factory)
        root = to_element(declaration)
        if self.mutate_xml:
            self.mutate_xml(root, factory.rng)
        payload = to_bytes(root)
        if self.corrupt_bytes:
            payload = payload[: len(payload) // 2]  # truncated in transit
        return GeneratedMessage(
            scenario=self.name,
            expected_outcome=self.expected_outcome,
            correlation_id=declaration.correlation_id,
            declaration_ref=declaration.declaration_ref,
            payload=payload,
            declaration=declaration,
        )


# ---------------- builders ----------------


def _standard(f: DeclarationFactory) -> Declaration:
    return f.declaration()


def _green_lane(f: DeclarationFactory) -> Declaration:
    d = f.declaration(
        movement_type="GB_TO_NI", ukims_authorised=True, controlled_goods=False, transport_mode="ROAD"
    )
    _cap_value(d, Decimal("50000"))
    return d


def _red_lane(f: DeclarationFactory) -> Declaration:
    return f.declaration(movement_type="GB_TO_NI", ukims_authorised=False)


def _lane_boundary(f: DeclarationFactory) -> Declaration:
    """Risk score exactly 40 (controlled goods only): must be RED (threshold is >= 40)."""
    d = f.declaration(
        movement_type="GB_TO_NI", ukims_authorised=True, controlled_goods=True, transport_mode="ROAD"
    )
    _cap_value(d, Decimal("50000"))
    return d


def _high_value(f: DeclarationFactory) -> Declaration:
    d = f.declaration(item_count=3)
    for item in d.goods_items:
        item.item_value = Decimal(f.rng.randint(4_000_000, 9_000_000)) / 100
    d.recalculate_totals()
    return d


def _multi_item(f: DeclarationFactory) -> Declaration:
    return f.declaration(item_count=f.rng.choice([25, 50, 99]))


def _unicode_text(f: DeclarationFactory) -> Declaration:
    d = f.declaration(movement_type=f.rng.choice(["EU_TO_NI", "NI_TO_EU"]))
    d.consignor.name = f.rng.choice(ref.UNICODE_TRADERS)
    d.consignee.name = f.rng.choice(ref.UNICODE_TRADERS)
    d.goods_items[0].description = f.rng.choice(ref.UNICODE_DESCRIPTIONS)
    return d


def _max_length_text(f: DeclarationFactory) -> Declaration:
    d = f.declaration(item_count=2)
    d.consignor.name = ("Maximum Length Consignor Trading Company " * 3)[:70]
    d.consignee.name = ("Maximum Length Consignee Holdings Group " * 3)[:70]
    d.goods_items[0].description = ("Assorted mixed consignment of household goods, " * 12)[:512]
    return d


def _minimal(f: DeclarationFactory) -> Declaration:
    """Every optional element omitted."""
    d = f.declaration(movement_type="NI_TO_GB", transport_mode="AIR")
    d.consignee.eori = None
    for party in (d.consignor, d.consignee):
        party.address.line2 = None
        party.address.postcode = None
    d.transport.vehicle_registration = None
    d.transport.port_of_entry = None
    return d


def _invoice_total_mismatch(f: DeclarationFactory) -> Declaration:
    d = f.declaration()
    d.total_invoice_value += Decimal("0.01")
    return d


def _net_exceeds_gross(f: DeclarationFactory) -> Declaration:
    d = f.declaration()
    d.total_gross_mass_kg = (sum(i.net_mass_kg for i in d.goods_items) - Decimal("0.001")).max(
        Decimal("0.001")
    )
    return d


def _gb_eori_for_ni_consignee(f: DeclarationFactory) -> Declaration:
    d = f.declaration(movement_type="GB_TO_NI")
    d.consignee.eori = "GB" + d.consignee.eori[2:]
    return d


def _item_sequence_gap(f: DeclarationFactory) -> Declaration:
    d = f.declaration(item_count=3)
    d.goods_items[-1].item_number = 7
    return d


def _arrival_too_far_ahead(f: DeclarationFactory) -> Declaration:
    from datetime import timedelta

    d = f.declaration()
    d.arrival_date = d.created_at.date() + timedelta(days=120)
    return d


def _cap_value(d: Declaration, cap: Decimal) -> None:
    for item in d.goods_items:
        item.item_value = min(item.item_value, cap / len(d.goods_items)).quantize(Decimal("0.01"))
    d.recalculate_totals()


# ---------------- XML mutators ----------------


def _lexical_variants(root: etree._Element, rng: random.Random) -> None:
    """Legal-but-unusual lexical forms: 1/0 booleans, trimmed zeros, a leading '+', padding."""
    ukims = root.find("gm:Declaration/gm:UkimsAuthorised", NSMAP)
    ukims.text = "1" if ukims.text == "true" else "0"
    for node in root.iterfind(".//gm:NetMassKg", NSMAP):
        node.text = node.text.rstrip("0").rstrip(".") or "0"
    gross = root.find("gm:Declaration/gm:TotalGrossMassKg", NSMAP)
    gross.text = f"  +{gross.text}  "


def _remove_declaration_ref(root: etree._Element, rng: random.Random) -> None:
    node = root.find("gm:Declaration/gm:DeclarationRef", NSMAP)
    node.getparent().remove(node)


def _bad_commodity_code(root: etree._Element, rng: random.Random) -> None:
    root.find(".//gm:CommodityCode", NSMAP).text = "8471-30"


# ---------------- catalogue ----------------

SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario("standard", "Random valid declaration across all four movement types", _standard),
        Scenario("green_lane", "GB->NI, UKIMS-authorised, no controlled goods: score 10, GREEN", _green_lane),
        Scenario("red_lane", "GB->NI without UKIMS authorisation: score >= 45, RED", _red_lane),
        Scenario("lane_boundary", "Risk score exactly 40 (threshold): must be RED", _lane_boundary),
        Scenario("high_value", "Invoice value over 100,000", _high_value),
        Scenario("multi_item", "25, 50 or 99 goods items (schema maximum)", _multi_item),
        Scenario(
            "unicode_text", "Irish, Welsh and European diacritics in names and descriptions", _unicode_text
        ),
        Scenario("max_length_text", "Names at 70 chars and a description at 512 chars", _max_length_text),
        Scenario("minimal", "All optional elements omitted", _minimal),
        Scenario(
            "lexical_variants",
            "Booleans as 1/0, decimals without trailing zeros, '+' sign and whitespace",
            _standard,
            mutate_xml=_lexical_variants,
        ),
        Scenario(
            "invalid_missing_ref",
            "DeclarationRef element removed",
            _standard,
            "SCHEMA_INVALID",
            mutate_xml=_remove_declaration_ref,
        ),
        Scenario(
            "invalid_commodity_code",
            "Commodity code not 8 or 10 digits",
            _standard,
            "SCHEMA_INVALID",
            mutate_xml=_bad_commodity_code,
        ),
        Scenario(
            "invalid_consignee_eori",
            "GB->NI with a GB (not XI) consignee EORI",
            _gb_eori_for_ni_consignee,
            "R001_CONSIGNEE_EORI",
        ),
        Scenario(
            "invalid_invoice_total",
            "Items do not sum to the invoice total",
            _invoice_total_mismatch,
            "R002_INVOICE_TOTAL",
        ),
        Scenario("invalid_mass", "Net mass exceeds gross mass", _net_exceeds_gross, "R003_MASS"),
        Scenario("invalid_item_sequence", "Item numbers 1, 2, 7", _item_sequence_gap, "R004_ITEM_SEQUENCE"),
        Scenario(
            "invalid_arrival_date",
            "Arrival 120 days after submission",
            _arrival_too_far_ahead,
            "R005_ARRIVAL_DATE",
        ),
        Scenario(
            "malformed_xml",
            "Payload truncated half way (not well-formed)",
            _standard,
            "MALFORMED_XML",
            corrupt_bytes=True,
        ),
    ]
}

# Default traffic mix (weights). Roughly 7% of messages are invalid by design.
DEFAULT_MIX: dict[str, int] = {
    "standard": 40,
    "green_lane": 10,
    "red_lane": 8,
    "lane_boundary": 5,
    "high_value": 4,
    "multi_item": 2,
    "unicode_text": 8,
    "max_length_text": 5,
    "minimal": 6,
    "lexical_variants": 5,
    "invalid_missing_ref": 1,
    "invalid_commodity_code": 1,
    "invalid_consignee_eori": 1,
    "invalid_invoice_total": 1,
    "invalid_mass": 1,
    "invalid_item_sequence": 1,
    "invalid_arrival_date": 1,
    "malformed_xml": 1,
}
VALID_MIX: dict[str, int] = {
    name: w for name, w in DEFAULT_MIX.items() if SCENARIOS[name].expected_outcome == ACCEPTED
}


def generate(
    count: int,
    mix: dict[str, int] | None = None,
    seed: int | None = None,
    factory: DeclarationFactory | None = None,
) -> list[GeneratedMessage]:
    """Generate `count` messages, choosing scenarios by weight."""
    factory = factory or DeclarationFactory(seed)
    weights = mix or DEFAULT_MIX
    unknown = set(weights) - set(SCENARIOS)
    if unknown:
        raise ValueError(f"unknown scenarios: {sorted(unknown)}")
    names = factory.rng.choices(list(weights), weights=list(weights.values()), k=count)
    return [SCENARIOS[name].generate(factory) for name in names]
