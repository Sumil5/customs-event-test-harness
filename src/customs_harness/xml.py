"""Namespace, hardened parser and XSD loading shared across the harness."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

NS = "urn:example:customs:goods-movement:v1"
NSMAP = {"gm": NS}

# No entity expansion and no network access: test tooling parses untrusted payloads too.
SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True)


def parse(payload: bytes) -> etree._Element:
    return etree.fromstring(payload, parser=SAFE_PARSER)


def qname(tag: str) -> str:
    return f"{{{NS}}}{tag}"


@lru_cache
def load_schema(path: Path) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(path)))


def schema_errors(payload: bytes, schema_path: Path) -> list[str]:
    """Empty list when the payload is well-formed and valid against the XSD."""
    try:
        doc = parse(payload)
    except etree.XMLSyntaxError as exc:
        return [f"malformed XML: {exc}"]
    schema = load_schema(schema_path)
    if schema.validate(doc):
        return []
    return [f"line {e.line}: {e.message}" for e in schema.error_log]
