"""Reconciliation outputs: JSON (machines), CSV (analysts), HTML (people), console summary."""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from rich.console import Console
from rich.table import Table

from customs_harness.reconciliation.engine import ReconciliationReport, Status

TEMPLATES = Environment(loader=PackageLoader("customs_harness", "templates"), autoescape=select_autoescape())


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def to_dict(report: ReconciliationReport, max_results: int = 5000) -> dict[str, Any]:
    return {
        "mapping": report.mapping,
        "topic": report.topic,
        "run_id": report.run_id,
        "since": _plain(report.since),
        "started_at": _plain(report.started_at),
        "duration_seconds": round(report.duration_seconds, 3),
        "passed": report.passed,
        "summary": {
            "messages_read": report.messages_read,
            "duplicate_messages": report.duplicate_messages,
            **{status.value.lower(): report.count(status) for status in Status},
            "orphans": len(report.orphans),
            "discrepancies": report.discrepancies,
        },
        "rejection_codes": dict(report.rejection_codes.most_common()),
        "column_mismatches": dict(report.column_mismatches.most_common()),
        "defect_signatures": [
            {"column": location, "signature": hint, "count": n}
            for (location, hint), n in report.defect_signatures.most_common()
        ],
        "results": [
            {
                "status": r.status.value,
                "correlation_id": r.correlation_id,
                "declaration_ref": r.key,
                "partition": r.partition,
                "offset": r.offset,
                "detail": r.detail,
                "diffs": [
                    {
                        "field": d.path,
                        "kind": d.kind,
                        "expected": _plain(d.expected),
                        "actual": _plain(d.actual),
                        "signature": d.hint,
                    }
                    for d in r.diffs
                ],
            }
            for r in report.results[:max_results]
        ],
        "orphans": report.orphans,
    }


def write_reports(report: ReconciliationReport, out_dir: Path, html_limit: int = 500) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "reconciliation.json",
        "csv": out_dir / "reconciliation-diffs.csv",
        "html": out_dir / "reconciliation.html",
    }
    paths["json"].write_text(json.dumps(to_dict(report), indent=2, ensure_ascii=False))

    with paths["csv"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["status", "declaration_ref", "correlation_id", "field", "expected", "actual", "signature"]
        )
        for result in report.results:
            for diff in result.diffs or [None]:
                writer.writerow(
                    [
                        result.status.value,
                        result.key,
                        result.correlation_id,
                        diff.path if diff else "",
                        _plain(diff.expected) if diff else "",
                        _plain(diff.actual) if diff else "",
                        (diff.hint or diff.kind) if diff else result.detail or "",
                    ]
                )

    problems = [r for r in report.results if r.status is not Status.REJECTED]
    signatures = report.defect_signatures.most_common()
    html = TEMPLATES.get_template("reconciliation.html.j2").render(
        r=report,
        counts={s.value: report.count(s) for s in Status},
        signatures=signatures,
        max_signature=max((n for _, n in signatures), default=1),
        rejections=report.rejection_codes.most_common(),
        problems=problems[:html_limit],
        shown=min(len(problems), html_limit),
        total_problems=len(problems),
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )
    paths["html"].write_text(html, encoding="utf-8")
    return paths


def print_summary(report: ReconciliationReport, console: Console | None = None) -> None:
    console = console or Console()
    verdict = (
        "[bold green]PASSED[/]"
        if report.passed
        else f"[bold red]FAILED[/] ({report.discrepancies} discrepancies)"
    )
    console.print(
        f"\nReconciliation of [bold]{report.messages_read:,}[/] messages from [cyan]{report.topic}[/] "
        f"in {report.duration_seconds:.2f}s: {verdict}"
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Outcome")
    table.add_column("Messages", justify="right")
    for status in Status:
        table.add_row(status.value, f"{report.count(status):,}")
    if report.two_way:
        table.add_row("ORPHANS", f"{len(report.orphans):,}")
    console.print(table)
    if report.defect_signatures:
        defects = Table(title="Defect signatures", show_header=True, header_style="bold")
        defects.add_column("Column")
        defects.add_column("Signature")
        defects.add_column("Messages", justify="right")
        for (location, hint), n in report.defect_signatures.most_common(15):
            defects.add_row(location, hint, f"{n:,}")
        console.print(defects)
