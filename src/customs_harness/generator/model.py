"""In-memory model of a Goods Movement Declaration, before it becomes XML."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class Address:
    line1: str
    town: str
    country_code: str
    line2: str | None = None
    postcode: str | None = None


@dataclass
class Party:
    name: str
    address: Address
    eori: str | None = None


@dataclass
class Transport:
    mode: str  # SEA | RAIL | ROAD | AIR
    vehicle_registration: str | None = None
    port_of_entry: str | None = None


@dataclass
class GoodsItem:
    item_number: int
    commodity_code: str
    description: str
    country_of_origin: str
    net_mass_kg: Decimal
    item_value: Decimal


@dataclass
class Declaration:
    message_id: str
    correlation_id: str
    created_at: datetime
    source_system: str
    declaration_ref: str
    category: str
    movement_type: str
    declarant_eori: str
    consignor: Party
    consignee: Party
    transport: Transport
    arrival_date: date
    ukims_authorised: bool
    invoice_currency: str
    goods_items: list[GoodsItem] = field(default_factory=list)
    total_gross_mass_kg: Decimal = Decimal("0")
    total_invoice_value: Decimal = Decimal("0")

    def recalculate_totals(self, gross_uplift: Decimal = Decimal("1.08")) -> None:
        """Keep header totals consistent with the items (business rules R002/R003)."""
        self.total_invoice_value = sum((i.item_value for i in self.goods_items), Decimal("0.00"))
        net = sum((i.net_mass_kg for i in self.goods_items), Decimal("0.000"))
        self.total_gross_mass_kg = (net * gross_uplift).quantize(Decimal("0.001"))
