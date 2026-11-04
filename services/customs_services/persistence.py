"""Persistence service: lands assessed declarations in Oracle.

assessed -> Oracle (DECLARATION + GOODS_ITEM), or rejected on failure.

Writes are batched (executemany with batch errors) and idempotent: a replayed
declaration hits the primary key and is skipped, so at-least-once delivery
never creates duplicates. Each declaration is all-or-nothing: if any of its
goods items fail, its header row is removed before the batch commits.
"""

from __future__ import annotations

import time
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

import oracledb
from lxml import etree

from customs_services.common.config import Settings
from customs_services.common.faults import Faults
from customs_services.common.logs import configure_logging, log_event
from customs_services.common.runtime import Inbound, Outbound, ServiceRunner, reject
from customs_services.common.xml import (
    NSMAP,
    boolean,
    decimal,
    parse,
    required_text,
    text,
    utc_datetime,
    xs_date,
)

TRANSPORT_MODE_CODES = {"SEA": 1, "RAIL": 2, "ROAD": 3, "AIR": 4}
CATEGORY_CODES = {"IMPORT": "IM", "EXPORT": "EX", "TRANSIT": "TR"}

DECLARATION_COLUMNS = (
    "declaration_ref", "correlation_id", "message_id", "message_created_at", "source_system",
    "declaration_category", "movement_type", "declarant_eori", "consignor_name", "consignor_country",
    "consignee_name", "consignee_eori", "consignee_town", "consignee_postcode", "consignee_country",
    "transport_mode_code", "vehicle_registration", "port_of_entry", "arrival_date", "ukims_authorised",
    "total_gross_mass_kg", "invoice_currency", "total_invoice_value", "goods_item_count",
    "risk_score", "lane", "assessed_at",
)  # fmt: skip
GOODS_ITEM_COLUMNS = (
    "declaration_ref", "item_number", "commodity_code", "description",
    "country_of_origin", "net_mass_kg", "item_value",
)  # fmt: skip


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})"


INSERT_DECLARATION = _insert_sql("declaration", DECLARATION_COLUMNS)
INSERT_GOODS_ITEM = _insert_sql("goods_item", GOODS_ITEM_COLUMNS)


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def to_rows(doc: etree._Element, faults: Faults) -> tuple[dict, list[dict]]:
    """Map an assessed message to a DECLARATION row and its GOODS_ITEM rows."""
    header = doc.find("gm:Header", NSMAP)
    decl = doc.find("gm:Declaration", NSMAP)
    assessment = doc.find("gm:Assessment", NSMAP)
    if assessment is None:
        raise ValueError("message has no Assessment element")

    ref = required_text(decl, "gm:DeclarationRef")
    created_at = utc_datetime(required_text(header, "gm:CreatedAt")).replace(tzinfo=None)
    gross_mass = decimal(decl, "gm:TotalGrossMassKg")
    consignor_name = required_text(decl, "gm:Consignor/gm:Name")
    consignee_name = required_text(decl, "gm:Consignee/gm:Name")
    items = decl.findall("gm:GoodsItems/gm:GoodsItem", NSMAP)

    if faults.enabled("drop_millis"):
        created_at = created_at.replace(microsecond=0)
    if faults.enabled("rounding"):
        gross_mass = gross_mass.quantize(Decimal("0.01"), ROUND_HALF_UP)
    if faults.enabled("ascii_names"):
        consignor_name, consignee_name = _ascii(consignor_name), _ascii(consignee_name)

    row = {
        "declaration_ref": ref,
        "correlation_id": required_text(header, "gm:CorrelationId"),
        "message_id": required_text(header, "gm:MessageId"),
        "message_created_at": created_at,
        "source_system": required_text(header, "gm:SourceSystem"),
        "declaration_category": CATEGORY_CODES[required_text(decl, "gm:DeclarationCategory")],
        "movement_type": required_text(decl, "gm:MovementType"),
        "declarant_eori": required_text(decl, "gm:DeclarantEori"),
        "consignor_name": consignor_name,
        "consignor_country": required_text(decl, "gm:Consignor/gm:Address/gm:CountryCode"),
        "consignee_name": consignee_name,
        "consignee_eori": text(decl, "gm:Consignee/gm:Eori"),
        "consignee_town": required_text(decl, "gm:Consignee/gm:Address/gm:Town"),
        "consignee_postcode": text(decl, "gm:Consignee/gm:Address/gm:Postcode"),
        "consignee_country": required_text(decl, "gm:Consignee/gm:Address/gm:CountryCode"),
        "transport_mode_code": TRANSPORT_MODE_CODES[required_text(decl, "gm:Transport/gm:Mode")],
        "vehicle_registration": text(decl, "gm:Transport/gm:VehicleRegistration"),
        "port_of_entry": text(decl, "gm:Transport/gm:PortOfEntry"),
        "arrival_date": xs_date(required_text(decl, "gm:ArrivalDate")),
        "ukims_authorised": "Y" if boolean(decl, "gm:UkimsAuthorised") else "N",
        "total_gross_mass_kg": gross_mass,
        "invoice_currency": required_text(decl, "gm:InvoiceCurrency"),
        "total_invoice_value": decimal(decl, "gm:TotalInvoiceValue"),
        "goods_item_count": len(items),
        "risk_score": int(required_text(assessment, "gm:RiskScore")),
        "lane": required_text(assessment, "gm:Lane"),
        "assessed_at": utc_datetime(required_text(assessment, "gm:AssessedAt")).replace(tzinfo=None),
    }

    item_rows = []
    for item in items:
        description = required_text(item, "gm:Description")
        if faults.enabled("truncate_description"):
            description = description[:100]
        item_rows.append(
            {
                "declaration_ref": ref,
                "item_number": int(required_text(item, "gm:ItemNumber")),
                "commodity_code": required_text(item, "gm:CommodityCode"),
                "description": description,
                "country_of_origin": required_text(item, "gm:CountryOfOrigin"),
                "net_mass_kg": decimal(item, "gm:NetMassKg"),
                "item_value": decimal(item, "gm:ItemValue"),
            }
        )
    if faults.enabled("skip_last_item") and len(item_rows) > 1:
        item_rows = item_rows[:-1]
    return row, item_rows


class DeclarationStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.connection = self._connect()

    def _connect(self) -> oracledb.Connection:
        last_error: Exception | None = None
        for _ in range(30):
            try:
                return oracledb.connect(
                    user=self.settings.oracle_user,
                    password=self.settings.oracle_password,
                    dsn=self.settings.oracle_dsn,
                )
            except oracledb.Error as exc:
                last_error = exc
                time.sleep(2)
        raise RuntimeError(f"could not connect to Oracle: {last_error}")

    def save(self, rows: list[tuple[dict, list[dict]]]) -> tuple[set[int], set[int], dict[int, str]]:
        """Insert declarations atomically. Returns (stored, duplicate, failed) batch indexes."""
        duplicates: set[int] = set()
        failed: dict[int, str] = {}
        with self.connection.cursor() as cursor:
            cursor.setinputsizes(
                message_created_at=oracledb.DB_TYPE_TIMESTAMP, assessed_at=oracledb.DB_TYPE_TIMESTAMP
            )
            cursor.executemany(INSERT_DECLARATION, [header for header, _ in rows], batcherrors=True)
            for error in cursor.getbatcherrors():
                if error.code == 1:  # ORA-00001: already stored -> idempotent replay
                    duplicates.add(error.offset)
                else:
                    failed[error.offset] = error.message
            inserted = [i for i in range(len(rows)) if i not in duplicates and i not in failed]

            item_rows, owners = [], []
            for index in inserted:
                for item in rows[index][1]:
                    item_rows.append(item)
                    owners.append(index)
            if item_rows:
                cursor.executemany(INSERT_GOODS_ITEM, item_rows, batcherrors=True)
                for error in cursor.getbatcherrors():
                    failed.setdefault(owners[error.offset], error.message)

            undo = [(rows[i][0]["declaration_ref"],) for i in inserted if i in failed]
            if undo:  # keep each declaration all-or-nothing
                cursor.executemany("DELETE FROM declaration WHERE declaration_ref = :1", undo)
        self.connection.commit()
        stored = {i for i in inserted if i not in failed}
        return stored, duplicates, failed


def main() -> None:
    settings = Settings.from_env()
    logger = configure_logging(settings)
    faults = Faults.parse(settings.faults)
    store = DeclarationStore(settings)

    def handle(batch: list[Inbound]) -> list[Outbound]:
        started = time.perf_counter()
        outputs: list[Outbound] = []
        pending: list[Inbound] = []
        rows: list[tuple[dict, list[dict]]] = []
        for msg in batch:
            try:
                header, items = to_rows(parse(msg.value), faults)
            except Exception as exc:
                outputs.append(reject(settings, msg, "PERSISTENCE_MAPPING_ERROR", repr(exc)))
                continue
            if faults.hits("drop", header["declaration_ref"]):
                continue  # the defect: acknowledged on Kafka, never stored, nothing logged
            pending.append(msg)
            rows.append((header, items))
        if not rows:
            return outputs

        stored, duplicates, failed = store.save(rows)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        for index, msg in enumerate(pending):
            fields = {
                "correlation_id": msg.correlation_id,
                "declaration_ref": rows[index][0]["declaration_ref"],
                "batch_size": len(batch),
                "batch_ms": elapsed_ms,
            }
            if index in stored:
                log_event(logger, "declaration.persisted", items=len(rows[index][1]), **fields)
            elif index in duplicates:
                log_event(logger, "declaration.duplicate_ignored", **fields)
            else:
                log_event(logger, "declaration.persist_failed", error=failed[index], **fields)
                outputs.append(reject(settings, msg, "PERSISTENCE_ERROR", failed[index]))
        return outputs

    ServiceRunner(settings, logger, settings.topics.assessed, handle).run()


if __name__ == "__main__":
    main()
