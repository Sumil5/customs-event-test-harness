# Five-minute walkthrough

A script for showing the project to someone. Run `make up` beforehand, and `make demo` once so that you have a report open.

## 1. The problem (30 s)

Messages flow through Kafka between several services before landing in Oracle. Three things can go wrong:

- a message gets **lost**
- it's stored **wrong**
- it's stored **too late** (the SLA is 13 seconds)

Spot-checking a handful of messages finds none of these reliably at thousands of messages a day.

Show the diagram in the README.

## 2. Generating traffic (1 min)

```bash
uv run harness scenarios
```

```bash
uv run harness generate -s unicode_text -o /tmp/demo
```

```bash
uv run harness publish -n 1000 --rate 200
```

Points to make:

- There are 18 scenarios, each with an expected outcome.
- Every payload is checked against the XSD before it's sent.
- Each run is tagged with `x-run-id`, so every tool can scope to exactly this run.

## 3. Reconciliation (1.5 min)

```bash
uv run harness reconcile
```

Open `reconciliation.html` and show the mapping file, [config/mappings/goods_movement_declaration.yaml](../config/mappings/goods_movement_declaration.yaml).

Points to make:

- The mapping is XPath to column, with types, code lookups and two derived rules recomputed independently.
- *Completeness*: every message is stored or dead-lettered with a reason.
- *Accuracy*: every column matches.

## 4. Proving it catches things (1.5 min)

```bash
make demo-faults
```

Open the failing reconciliation report and walk the defect signatures table, with one real-world cause per row. Then open the SLA report and show the bottleneck stage and the breaches.

## 5. Investigating one message (30 s)

```bash
uv run harness timeline <correlation-id-from-the-report>
```

A lost message shows intake and assessment logging it, then nothing from persistence. A breach shows the service's own processing taking 0.3 ms while the message waited 14 s behind a slow one: head-of-line blocking that per-service metrics hide.

## 6. How it runs in CI (30 s)

The GitLab pipeline runs lint, then unit tests, then the end-to-end suite and the fault-injection suite. The fault-injection job is a test of the tests: it passes only if every injected defect is detected. The latest reports are published to GitLab Pages.
