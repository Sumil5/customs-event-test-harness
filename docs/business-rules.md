# Business rules

This is the specification both sides work from. The services implement it. The harness re-implements the derived rules independently in [`reconciliation/rules.py`](../src/customs_harness/reconciliation/rules.py), so it can act as a test oracle.

These rules are **illustrative**. They are loosely inspired by public concepts such as green and red lanes for goods moving into Northern Ireland, XI EORIs and UKIMS authorisation, but they are not real customs policy.

## Intake validation

The intake service checks rules in order and rejects on the first failure. Rejected messages go to `customs.declarations.rejected`.

| Code | Rule |
|---|---|
| `MALFORMED_XML` | The payload must be well-formed XML |
| `SCHEMA_INVALID` | The payload must be valid against `goods-movement-declaration-v1.xsd` |
| `R001_CONSIGNEE_EORI` | For `GB_TO_NI` and `EU_TO_NI` movements, the consignee must have an EORI starting `XI` |
| `R002_INVOICE_TOTAL` | The sum of `ItemValue` must equal `TotalInvoiceValue` exactly |
| `R003_MASS` | The sum of `NetMassKg` must not exceed `TotalGrossMassKg` |
| `R004_ITEM_SEQUENCE` | `ItemNumber`s must run 1, 2, … n, in order |
| `R005_ARRIVAL_DATE` | `ArrivalDate` must be within 30 days before or 90 days after the `CreatedAt` date |

## Risk score and lane

The score starts at **10**. Add:

| Condition | Points |
|---|---|
| `MovementType` is `GB_TO_NI` and the declaration is **not** UKIMS-authorised | +35 |
| Any goods item's commodity code starts with a controlled chapter: `01` live animals, `02` meat, `03` fish, `04` dairy | +30 |
| `TotalInvoiceValue` is over 100,000 | +15 |
| Transport mode is `AIR` | +5 |

Cap the score at 100.

**Lane** is `RED` if the score is **40 or more**, otherwise `GREEN`. A score of exactly 40 is red. This is the boundary that the `lane_boundary` scenario and fault exercise.

## Stored code mappings

| Message value | Oracle column | Stored as |
|---|---|---|
| `DeclarationCategory` IMPORT / EXPORT / TRANSIT | `DECLARATION_CATEGORY` | IM / EX / TR |
| `Transport/Mode` SEA / RAIL / ROAD / AIR | `TRANSPORT_MODE_CODE` | 1 / 2 / 3 / 4 |
| `UkimsAuthorised` true / false (or 1 / 0) | `UKIMS_AUTHORISED` | Y / N |
| `CreatedAt`, `AssessedAt` (UTC) | `*_AT` | `TIMESTAMP(3)`, UTC |
