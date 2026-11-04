"""SLA report outputs: JSON, HTML and a console summary, plus a single-message timeline."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from rich.console import Console
from rich.table import Table

from customs_harness.tracing.cloudwatch import LogEvent
from customs_harness.tracing.collector import Trace
from customs_harness.tracing.sla import LatencyStats, SlaReport

TEMPLATES = Environment(loader=PackageLoader("customs_harness", "templates"), autoescape=select_autoescape())

STALL_MEANINGS = {
    "not submitted": "never reached the inbound topic (publishing problem)",
    "submitted": "accepted onto Kafka but intake never forwarded or rejected it",
    "validated": "validated but the assessment service never emitted it",
    "assessed": "assessed but never stored in Oracle: look at the persistence service",
}


def fmt_ms(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value / 1000:.2f}s" if value >= 1000 else f"{value:.0f}ms"


def _stats(stats: LatencyStats) -> dict[str, Any]:
    return {
        k: (None if isinstance(v, float) and math.isnan(v) else round(v, 1)) for k, v in vars(stats).items()
    }


def to_dict(report: SlaReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "sla_seconds": report.sla_seconds,
        "passed": report.passed,
        "outcomes": dict(report.outcomes),
        "end_to_end_ms": _stats(report.end_to_end),
        "stages_ms": {hop: _stats(s) for hop, s in report.hops.items()},
        "bottleneck": report.bottleneck,
        "breaches": [
            {
                "correlation_id": t.correlation_id,
                "declaration_ref": t.declaration_ref,
                "end_to_end_ms": round(t.end_to_end_ms, 1),
                "slowest_stage": t.slowest_hop,
                "stages_ms": {k: round(v, 1) for k, v in t.hop_ms.items()},
            }
            for t in report.breaches
        ],
        "breach_causes": dict(report.breach_causes),
        "incomplete": [
            {
                "correlation_id": t.correlation_id,
                "declaration_ref": t.declaration_ref,
                "last_stage": t.last_stage,
            }
            for t in report.incomplete
        ],
        "stalled_after": dict(report.stalled_after),
        "anomalies": [{"correlation_id": t.correlation_id, "issues": t.anomalies} for t in report.anomalies],
        "histogram": dict(report.histogram),
    }


def write_reports(report: SlaReport, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / "sla.json", "html": out_dir / "sla.html"}
    paths["json"].write_text(json.dumps(to_dict(report), indent=2))
    html = TEMPLATES.get_template("sla.html.j2").render(
        r=report,
        e2e=report.end_to_end,
        hops=report.hops,
        outcomes={"PERSISTED": 0, "REJECTED": 0, **report.outcomes},
        max_bucket=max((n for _, n in report.histogram), default=1) or 1,
        meanings=STALL_MEANINGS,
        ms=fmt_ms,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )
    paths["html"].write_text(html, encoding="utf-8")
    return paths


def print_summary(report: SlaReport, console: Console | None = None) -> None:
    console = console or Console()
    verdict = "[bold green]PASSED[/]" if report.passed else "[bold red]FAILED[/]"
    console.print(
        f"\nSLA {report.sla_seconds:g}s over [bold]{len(report.traces):,}[/] traced messages: {verdict}  "
        f"({len(report.breaches)} breaches, {len(report.incomplete)} incomplete)"
    )
    table = Table(show_header=True, header_style="bold")
    for column in ("Stage", "n", "p50", "p95", "p99", "max"):
        table.add_column(column, justify="left" if column == "Stage" else "right")
    for hop, stats in [*report.hops.items(), ("end to end", report.end_to_end)]:
        label = f"{hop} [yellow](bottleneck)[/]" if hop == report.bottleneck else hop
        table.add_row(
            label,
            f"{stats.count:,}",
            fmt_ms(stats.p50),
            fmt_ms(stats.p95),
            fmt_ms(stats.p99),
            fmt_ms(stats.max),
        )
    console.print(table)
    if report.incomplete:
        for stage, n in report.stalled_after.most_common():
            console.print(
                f"  [red]{n}[/] messages stopped after [bold]{stage}[/]: {STALL_MEANINGS.get(stage, '')}"
            )
    if report.anomalies:
        console.print(
            f"  [red]{len(report.anomalies)}[/] traces have anomalies, "
            f"e.g. {report.anomalies[0].anomalies[0]} (key collision with an earlier run?)"
        )


def print_timeline(
    trace: Trace, logs: list[LogEvent], sla_seconds: float, console: Console | None = None
) -> None:
    """One message's journey: Kafka hops, Oracle commit and CloudWatch log lines, in time order."""
    console = console or Console()
    rows: list[tuple[datetime, str, str]] = [
        (ts, "kafka" if s != "persisted" else "oracle", s) for s, ts in trace.events.items()
    ]
    for event in logs:
        detail = {
            k: v for k, v in event.fields.items() if k in ("rejection_code", "duration_ms", "items", "error")
        }
        extra = " ".join(f"{k}={v}" for k, v in detail.items() if v is not None)
        rows.append((event.timestamp, "cloudwatch", f"{event.service}: {event.event} {extra}".rstrip()))
    rows.sort(key=lambda row: row[0])
    start = trace.events.get("submitted") or (rows[0][0] if rows else None)

    table = Table(title=f"Correlation ID {trace.correlation_id}", show_header=True, header_style="bold")
    table.add_column("Time (UTC)")
    table.add_column("+ms", justify="right")
    table.add_column("Source")
    table.add_column("Event")
    for ts, source, label in rows:
        offset = (ts - start).total_seconds() * 1000 if start else 0
        table.add_row(ts.strftime("%H:%M:%S.%f")[:-3], f"{offset:.0f}", source, label)
    console.print(table)
    e2e = trace.end_to_end_ms
    if e2e is None:
        console.print(
            f"[red]Incomplete[/]: last stage reached was [bold]{trace.last_stage}[/]. "
            f"{STALL_MEANINGS.get(trace.last_stage, '')}"
        )
    else:
        ok = e2e <= sla_seconds * 1000
        console.print(
            f"{trace.outcome.lower()} in [bold]{fmt_ms(e2e)}[/] "
            f"({'[green]within' if ok else '[red]BREACHES'}[/] the {sla_seconds:g}s SLA)"
        )
