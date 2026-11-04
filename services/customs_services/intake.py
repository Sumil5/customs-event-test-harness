"""Intake service: schema and business-rule validation.

inbound -> validated (accepted) or rejected (dead-letter, with a reason code)
"""

from __future__ import annotations

import time
from datetime import timedelta

from lxml import etree

from customs_services.common.config import Settings
from customs_services.common.logs import configure_logging, log_event
from customs_services.common.runtime import Inbound, Outbound, ServiceRunner, forward, reject
from customs_services.common.xml import (
    NSMAP,
    decimal,
    load_schema,
    parse,
    required_text,
    text,
    utc_datetime,
    xs_date,
)

NI_DESTINATIONS = {"GB_TO_NI", "EU_TO_NI"}
ARRIVAL_WINDOW = (timedelta(days=-30), timedelta(days=90))


def business_rule_violation(doc: etree._Element) -> tuple[str, str] | None:
    """Rules XSD cannot express. Returns (code, reason) for the first violation."""
    decl = doc.find("gm:Declaration", NSMAP)
    movement = required_text(decl, "gm:MovementType")
    consignee_eori = text(decl, "gm:Consignee/gm:Eori") or ""
    if movement in NI_DESTINATIONS and not consignee_eori.startswith("XI"):
        return "R001_CONSIGNEE_EORI", f"{movement} requires an XI EORI for the consignee"

    items = decl.findall("gm:GoodsItems/gm:GoodsItem", NSMAP)
    item_total = sum(decimal(i, "gm:ItemValue") for i in items)
    declared_total = decimal(decl, "gm:TotalInvoiceValue")
    if item_total != declared_total:
        return "R002_INVOICE_TOTAL", f"item values sum to {item_total}, TotalInvoiceValue is {declared_total}"

    net_mass = sum(decimal(i, "gm:NetMassKg") for i in items)
    gross_mass = decimal(decl, "gm:TotalGrossMassKg")
    if net_mass > gross_mass:
        return "R003_MASS", f"net mass {net_mass}kg exceeds gross mass {gross_mass}kg"

    numbers = [int(required_text(i, "gm:ItemNumber")) for i in items]
    if numbers != list(range(1, len(numbers) + 1)):
        return "R004_ITEM_SEQUENCE", f"item numbers must run 1..{len(numbers)}, got {numbers}"

    created = utc_datetime(required_text(doc, "gm:Header/gm:CreatedAt")).date()
    arrival = xs_date(required_text(decl, "gm:ArrivalDate"))
    earliest, latest = (created + delta for delta in ARRIVAL_WINDOW)
    if not earliest <= arrival <= latest:
        return "R005_ARRIVAL_DATE", f"arrival date {arrival} outside {earliest}..{latest}"
    return None


def main() -> None:
    settings = Settings.from_env()
    logger = configure_logging(settings)
    schema = load_schema(settings.schema_path)

    def validate(msg: Inbound) -> Outbound:
        try:
            doc = parse(msg.value)
        except etree.XMLSyntaxError as exc:
            return reject(settings, msg, "MALFORMED_XML", str(exc))
        if not schema.validate(doc):
            error = schema.error_log.last_error
            return reject(settings, msg, "SCHEMA_INVALID", f"line {error.line}: {error.message}")
        violation = business_rule_violation(doc)
        if violation:
            return reject(settings, msg, *violation)
        return forward(msg, settings.topics.validated)

    def handle(batch: list[Inbound]) -> list[Outbound]:
        outputs = []
        for msg in batch:
            started = time.perf_counter()
            out = validate(msg)
            outcome = "rejected" if out.topic == settings.topics.rejected else "accepted"
            log_event(
                logger,
                f"declaration.{outcome}",
                correlation_id=msg.correlation_id,
                declaration_ref=(msg.key or b"").decode(),
                rejection_code=out.headers.get("x-rejection-code"),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            outputs.append(out)
        return outputs

    ServiceRunner(settings, logger, settings.topics.inbound, handle).run()


if __name__ == "__main__":
    main()
