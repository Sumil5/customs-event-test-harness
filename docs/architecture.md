# Architecture and design

## The pipeline (system under test)

| Topic | Producer | Consumer | Content |
|---|---|---|---|
| `customs.declarations.inbound` | payload generator | intake | submitted XML |
| `customs.declarations.validated` | intake | assessment | the same XML, accepted |
| `customs.declarations.assessed` | assessment | persistence | XML with an `<Assessment>` element appended |
| `customs.declarations.rejected` | any service | (dead-letter) | original payload + `x-rejection-code`, `x-rejection-reason`, `x-rejected-by`, `x-source-topic` headers |

Every topic has three partitions and is keyed by `DeclarationRef`, so each declaration's messages stay in order. Every message carries a `correlation_id` header (it is also in the XML header), plus `x-run-id` and `x-scenario` from the generator. The services copy headers forward unchanged.

### Delivery semantics

Each service runs the same consume → transform → produce loop ([`runtime.py`](../services/customs_services/common/runtime.py)):

1. Consume up to 500 messages.
2. Process them, producing results with an idempotent producer.
3. `flush()` and wait for every delivery report.
4. Only then commit the consumer offsets, synchronously.

That gives **at-least-once** delivery: if a service crashes mid-batch, the batch is replayed. The persistence service is therefore **idempotent**. A replayed declaration hits the primary key (`ORA-00001`), is detected through `executemany(..., batcherrors=True)`, and is skipped. Each declaration is also all-or-nothing: if any of its goods items fail, its header row is deleted before the batch commits.

### Oracle schema

[`01_customs_schema.sql`](../infra/oracle/init/01_customs_schema.sql) creates two tables:

- `DECLARATION`: one row per accepted declaration. It holds the code-mapped fields (category `IM/EX/TR`, transport mode `1–4`, UKIMS `Y/N`), the derived `RISK_SCORE` and `LANE`, and `PERSISTED_AT`, the UTC commit time that marks the end of each traced journey.
- `GOODS_ITEM`: one row per goods item, keyed by `(DECLARATION_REF, ITEM_NUMBER)`, with `ON DELETE CASCADE`.

## Reconciliation

```text
for each batch of 500 messages read from the topic (bounded, read-only):
    parse XML (XXE-safe) -> business key, correlation ID
    extract expected values per the YAML mapping:
        XPath -> lookup (code table) -> type coercion (decimal, date, UTC datetime, Y/N ...)
        or a derived rule recomputed independently by the harness (risk score, lane)
    fetch DECLARATION rows by key (one IN-list), GOODS_ITEM rows by parent key (one IN-list)
    classify each message:
        stored             -> compare every column; match child rows by item key
                              MATCHED | MISMATCHED (with field diffs + defect signature)
        not stored         -> on the dead-letter topic?  REJECTED (with reason) : MISSING
        unreadable / no key -> dead-lettered?  REJECTED : UNPARSEABLE
optional two-way check: rows stored in the window that no message explains -> ORPHANS
```

Comparison rules ([`compare.py`](../src/customs_harness/reconciliation/compare.py)):

- **Decimals** compare numerically (`12.5 == 12.500`). An optional `tolerance` is opt-in per column.
- **Strings** compare exactly, whitespace included, except that `''` equals `NULL`, because that is how Oracle stores an empty string.
- **`TIMESTAMP`** values are read as UTC. **`DATE`** columns compare as dates.
- **Child rows** match on their item key, and both missing and unexpected rows are reported.

Each mismatch gets a *defect signature* where one can be inferred: rounded or truncated to *n* dp, truncated to *n* chars, non-ASCII characters lost, whitespace or letter case differs, fractional seconds lost, shifted by *n* hours (a time zone problem), off by *n* days, not stored (`NULL`), or the service disagreeing with a named rule. The report groups mismatches by signature, so 900 broken messages show up as the 7 defects that broke them.

Before reading anything, the engine checks the mapping against `USER_TAB_COLUMNS`. If the mapping names a column that doesn't exist (schema drift), it fails immediately rather than reporting thousands of false mismatches.

## Tracing and the SLA

Kafka record timestamps mark when each stage's output was accepted by the broker. The topics use `LogAppendTime`, so these timestamps come from the broker's clock. Oracle's `PERSISTED_AT` marks the end:

```text
submitted --intake--> validated --assessment--> assessed --persistence--> persisted
          \-----------------------------------------------------------> rejected
```

- **End-to-end time** runs from `submitted` to `persisted`, or to `rejected` for invalid messages. The SLA applies to **every message**, not to a percentile. A single slow message is a breach even when the p95 looks fine, and there is a unit test that checks exactly that.
- **Bottleneck** is the stage with the highest p95. **Breach causes** gives the slowest stage of each breaching message.
- **Incomplete journeys** report the last stage reached. For example, "stopped after assessed" points straight at the persistence service. This is how lost messages are isolated.
- **Anomalies** are rows committed *before* their message was sent. That means a key collision or a replay, not this message's write, so the measurement is distrusted and the run fails.
- **Start time and clocks.** Every Kafka timestamp is the broker's `LogAppendTime`, so client clocks never enter the calculation. That covers the harness running on a Mac while the broker runs in a VM, and a producer that takes about 0.5 s to start sending. A test checks that each stage topic really uses `LogAppendTime`. The final timestamp comes from Oracle's clock, so across real hosts you would still allow for NTP skew between the broker and the database (`CLOCK_TOLERANCE`).

### CloudWatch

The services ship one JSON log line per message per stage to CloudWatch Logs groups `/customs/<service>`, via `watchtower`. Locally the destination is moto. `harness timeline` runs the same filter you would type into the CloudWatch console (`"<correlation-id>"`) across the three log groups, and merges the results with the Kafka and Oracle timestamps. To point it at real AWS, set `AWS_ENDPOINT_URL=` (empty) and use normal credentials.

## Payload generator

`DeclarationFactory` builds a valid declaration from a seed using Faker locales (en_GB, en_IE, fr_FR, de_DE and nl_NL) and reference data: commodity codes, UN/LOCODE ports, GB, NI and Irish vehicle registrations, and BT postcodes. Each of the 18 [scenarios](../src/customs_harness/generator/scenarios.py) either tweaks the model or mutates the rendered XML, and declares its expected outcome. Valid scenarios include:

- green lane, red lane, and the exact score-40 lane boundary
- high value
- 99 items (the schema maximum)
- Irish, Welsh and European diacritics
- maximum-length fields
- every optional element omitted
- unusual lexical forms (`1`/`0` booleans, `+` signs, padded decimals)

The invalid scenarios cover each of the five business rules, two schema violations and a truncated payload.

The seed reproduces content but **never identifiers**. Re-using keys across runs would collide with rows already in Oracle, and the idempotent pipeline would silently ignore the new messages.

## Fault injection

[`faults.py`](../services/customs_services/common/faults.py) holds the catalogue. Faults are switched on per service through `PERSISTENCE_FAULTS` and `ASSESSMENT_FAULTS`. Rate-based faults (`drop`, `delay`) select messages by a SHA-256 hash of the declaration reference, so tests can predict exactly which messages are affected.

## CI/CD

```text
lint ──> unit-tests ──> e2e-tests (DinD: compose stack + containerised harness)  ──> pages
                    └─> fault-injection (same, with defects on: must detect all)
```

The infrastructure images bake their init scripts in rather than bind-mounting them, because a Docker-in-Docker daemon can't see the job's files. Reports are copied out of the harness container with `docker cp` for the same reason. Docker Hub pulls go through `mirror.gcr.io` to avoid anonymous rate limits.

Image builds and pulls depend on PyPI and Docker Hub, so the pipelines retry them up to three times with [`scripts/retry.sh`](../scripts/retry.sh). A shared runner occasionally loses the network for a minute. Test steps are never retried, because retrying tests would hide flakiness, and flaky tests are exactly what a harness must not tolerate.
