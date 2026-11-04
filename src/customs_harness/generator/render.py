"""Model -> XML. Element order follows the XSD sequence exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from lxml import etree

from customs_harness.generator.model import Address, Declaration, Party
from customs_harness.xml import NS, qname


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _el(parent: etree._Element, tag: str, value: object | None = None) -> etree._Element:
    element = etree.SubElement(parent, qname(tag))
    if value is not None:
        element.text = str(value)
    return element


def _opt(parent: etree._Element, tag: str, value: object | None) -> None:
    if value is not None:
        _el(parent, tag, value)


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _mass(value: Decimal) -> str:
    return f"{value:.3f}"


def _address(parent: etree._Element, address: Address) -> None:
    node = _el(parent, "Address")
    _el(node, "Line1", address.line1)
    _opt(node, "Line2", address.line2)
    _el(node, "Town", address.town)
    _opt(node, "Postcode", address.postcode)
    _el(node, "CountryCode", address.country_code)


def _party(parent: etree._Element, tag: str, party: Party) -> None:
    node = _el(parent, tag)
    _el(node, "Name", party.name)
    _opt(node, "Eori", party.eori)
    _address(node, party.address)


def to_element(d: Declaration) -> etree._Element:
    root = etree.Element(qname("GoodsMovementDeclaration"), nsmap={None: NS})
    root.set("schemaVersion", "1.0")

    header = _el(root, "Header")
    _el(header, "MessageId", d.message_id)
    _el(header, "CorrelationId", d.correlation_id)
    _el(header, "CreatedAt", utc_iso(d.created_at))
    _el(header, "SourceSystem", d.source_system)

    body = _el(root, "Declaration")
    _el(body, "DeclarationRef", d.declaration_ref)
    _el(body, "DeclarationCategory", d.category)
    _el(body, "MovementType", d.movement_type)
    _el(body, "DeclarantEori", d.declarant_eori)
    _party(body, "Consignor", d.consignor)
    _party(body, "Consignee", d.consignee)
    transport = _el(body, "Transport")
    _el(transport, "Mode", d.transport.mode)
    _opt(transport, "VehicleRegistration", d.transport.vehicle_registration)
    _opt(transport, "PortOfEntry", d.transport.port_of_entry)
    _el(body, "ArrivalDate", d.arrival_date.isoformat())
    _el(body, "UkimsAuthorised", "true" if d.ukims_authorised else "false")
    _el(body, "TotalGrossMassKg", _mass(d.total_gross_mass_kg))
    _el(body, "InvoiceCurrency", d.invoice_currency)
    _el(body, "TotalInvoiceValue", _money(d.total_invoice_value))

    items = _el(body, "GoodsItems")
    for item in d.goods_items:
        node = _el(items, "GoodsItem")
        _el(node, "ItemNumber", item.item_number)
        _el(node, "CommodityCode", item.commodity_code)
        _el(node, "Description", item.description)
        _el(node, "CountryOfOrigin", item.country_of_origin)
        _el(node, "NetMassKg", _mass(item.net_mass_kg))
        _el(node, "ItemValue", _money(item.item_value))
    return root


def to_bytes(root: etree._Element, pretty: bool = False) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=pretty)
