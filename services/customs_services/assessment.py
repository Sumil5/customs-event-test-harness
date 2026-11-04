"""Assessment service: risk-scores each declaration and routes it to a lane.

validated -> assessed (with an <Assessment> element appended)

The rules are illustrative, loosely inspired by green/red lane routing for
goods moving into Northern Ireland. They are not real customs policy.
"""

from __future__ import annotations

import time
from decimal import Decimal

from lxml import etree

from customs_services.common.config import Settings
from customs_services.common.faults import Faults
from customs_services.common.logs import configure_logging, log_event
from customs_services.common.runtime import Inbound, Outbound, ServiceRunner, forward, reject
from customs_services.common.xml import (
    NSMAP,
    boolean,
    decimal,
    parse,
    qname,
    required_text,
    serialise,
    utc_now_iso,
)

CONTROLLED_CHAPTERS = frozenset({"01", "02", "03", "04"})  # live animals, meat, fish, dairy
HIGH_VALUE_THRESHOLD = Decimal("100000")
RED_LANE_THRESHOLD = 40


def risk_score(doc: etree._Element) -> int:
    decl = doc.find("gm:Declaration", NSMAP)
    score = 10
    if required_text(decl, "gm:MovementType") == "GB_TO_NI" and not boolean(decl, "gm:UkimsAuthorised"):
        score += 35
    chapters = {
        code[:2]
        for code in decl.xpath("gm:GoodsItems/gm:GoodsItem/gm:CommodityCode/text()", namespaces=NSMAP)
    }
    if chapters & CONTROLLED_CHAPTERS:
        score += 30
    if decimal(decl, "gm:TotalInvoiceValue") > HIGH_VALUE_THRESHOLD:
        score += 15
    if required_text(decl, "gm:Transport/gm:Mode") == "AIR":
        score += 5
    return min(score, 100)


def lane_for(score: int, faults: Faults) -> str:
    if faults.enabled("lane_boundary"):
        return "RED" if score > RED_LANE_THRESHOLD else "GREEN"  # the defect
    return "RED" if score >= RED_LANE_THRESHOLD else "GREEN"


def main() -> None:
    settings = Settings.from_env()
    logger = configure_logging(settings)
    faults = Faults.parse(settings.faults)

    def assess(msg: Inbound) -> Outbound:
        doc = parse(msg.value)
        for existing in doc.findall("gm:Assessment", NSMAP):  # replayed message
            doc.remove(existing)
        score = risk_score(doc)
        assessment = etree.SubElement(doc, qname("Assessment"))
        etree.SubElement(assessment, qname("RiskScore")).text = str(score)
        etree.SubElement(assessment, qname("Lane")).text = lane_for(score, faults)
        etree.SubElement(assessment, qname("AssessedAt")).text = utc_now_iso()
        return forward(msg, settings.topics.assessed, value=serialise(doc))

    def handle(batch: list[Inbound]) -> list[Outbound]:
        outputs = []
        for msg in batch:
            started = time.perf_counter()
            ref = (msg.key or b"").decode()
            if faults.hits("delay", ref):
                time.sleep(faults.delay_ms() / 1000)  # blocks the whole batch, as a slow call would
            try:
                out = assess(msg)
                event = "declaration.assessed"
            except Exception as exc:
                out = reject(settings, msg, "ASSESSMENT_ERROR", repr(exc))
                event = "declaration.assessment_failed"
            log_event(
                logger,
                event,
                correlation_id=msg.correlation_id,
                declaration_ref=ref,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            outputs.append(out)
        return outputs

    ServiceRunner(settings, logger, settings.topics.validated, handle).run()


if __name__ == "__main__":
    main()
