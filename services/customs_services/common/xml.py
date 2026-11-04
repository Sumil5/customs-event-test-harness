"""XML helpers shared by the services."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from lxml import etree

NS = "urn:example:customs:goods-movement:v1"
NSMAP = {"gm": NS}

# Hardened parser: no entity expansion, no network access (XXE-safe).
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True)


def parse(payload: bytes) -> etree._Element:
    return etree.fromstring(payload, parser=_PARSER)


def serialise(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def load_schema(path: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(path))


def qname(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def text(node: etree._Element, path: str) -> str | None:
    """Raw element text. Free text is never stripped: storing it altered would be a defect."""
    return node.findtext(path, namespaces=NSMAP)


def required_text(node: etree._Element, path: str) -> str:
    value = text(node, path)
    if value is None:
        raise ValueError(f"missing element {path}")
    return value


# Typed readers strip whitespace, as XSD's "collapse" rule does for these types.
def decimal(node: etree._Element, path: str) -> Decimal:
    return Decimal(required_text(node, path).strip())


def boolean(node: etree._Element, path: str) -> bool:
    return required_text(node, path).strip() in {"true", "1"}  # xs:boolean: true/false/1/0


def xs_date(value: str) -> date:
    # xs:date may carry a timezone suffix ("2026-11-20Z", "2026-11-20+01:00")
    return date.fromisoformat(value.strip()[:10])


def utc_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.strip()).astimezone(UTC)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
