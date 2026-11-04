"""Test oracle for derived columns: an independent implementation of the routing rules.

These are written from the rules as specified (docs/business-rules.md), not
imported from the service, so that a defect in the service's implementation
shows up as a mismatch instead of being copied into the expectation.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from lxml import etree

CONTROLLED_CHAPTERS = frozenset({"01", "02", "03", "04"})  # live animals, meat, fish, dairy
HIGH_VALUE_THRESHOLD = Decimal("100000")
RED_LANE_THRESHOLD = 40  # a score AT the threshold is RED


def _string(doc: etree._Element, ns: dict[str, str], path: str) -> str:
    return doc.xpath(f"string({path})", namespaces=ns).strip()


def expected_risk_score(doc: etree._Element, ns: dict[str, str]) -> int:
    score = 10
    movement = _string(doc, ns, "gm:Declaration/gm:MovementType")
    ukims = _string(doc, ns, "gm:Declaration/gm:UkimsAuthorised") in ("true", "1")
    if movement == "GB_TO_NI" and not ukims:
        score += 35
    codes = doc.xpath("gm:Declaration/gm:GoodsItems/gm:GoodsItem/gm:CommodityCode/text()", namespaces=ns)
    if any(code.strip()[:2] in CONTROLLED_CHAPTERS for code in codes):
        score += 30
    if Decimal(_string(doc, ns, "gm:Declaration/gm:TotalInvoiceValue")) > HIGH_VALUE_THRESHOLD:
        score += 15
    if _string(doc, ns, "gm:Declaration/gm:Transport/gm:Mode") == "AIR":
        score += 5
    return min(score, 100)


def lane_for_score(score: int) -> str:
    return "RED" if score >= RED_LANE_THRESHOLD else "GREEN"


def expected_lane(doc: etree._Element, ns: dict[str, str]) -> str:
    return lane_for_score(expected_risk_score(doc, ns))


DERIVED_RULES: dict[str, Callable[[etree._Element, dict[str, str]], Any]] = {
    "risk_score": expected_risk_score,
    "lane": expected_lane,
}
