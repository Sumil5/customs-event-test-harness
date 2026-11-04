"""Command line entry point:  `harness --help`

Typical flow:
    harness publish -n 2000 --rate 200     # generate + publish a tagged run
    harness reconcile                      # Kafka vs Oracle, field by field (latest run)
    harness trace                          # correlation-ID latency vs the 13s SLA
    harness run -n 2000                    # all of the above in one go
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from customs_harness.config import Settings, get_settings
from customs_harness.generator.factory import DeclarationFactory
from customs_harness.generator.render import to_bytes
from customs_harness.generator.runs import RunManifest, publish_run, wait_for_run
from customs_harness.generator.scenarios import DEFAULT_MIX, SCENARIOS, VALID_MIX, generate
from customs_harness.oracle import OracleClient
from customs_harness.reconciliation import report as recon_report
from customs_harness.reconciliation.engine import ReconciliationEngine
from customs_harness.reconciliation.mapping import Mapping
from customs_harness.tracing import report as sla_report
from customs_harness.tracing.cloudwatch import find_log_events
from customs_harness.tracing.collector import collect_traces
from customs_harness.tracing.sla import SlaReport
from customs_harness.xml import parse

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
console = Console()


# ---------------- helpers ----------------


def _parse_mix(mix: str) -> dict[str, int]:
    if mix == "default":
        return DEFAULT_MIX
    if mix == "valid":
        return VALID_MIX
    weights = {}
    for part in mix.split(","):
        name, _, weight = part.strip().partition("=")
        weights[name] = int(weight or 1)
    unknown = set(weights) - set(SCENARIOS)
    if unknown:
        raise typer.BadParameter(f"unknown scenario(s) {sorted(unknown)}; see `harness scenarios`")
    return weights


def _parse_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    if value == "today":
        return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if value.endswith(("m", "h")) and value[:-1].isdigit():  # e.g. 30m, 2h
        unit = {"m": "minutes", "h": "hours"}[value[-1]]
        return datetime.now(UTC) - timedelta(**{unit: int(value[:-1])})
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _latest_file(settings: Settings) -> Path:
    return settings.reports_dir / "LATEST"


def _resolve_run(settings: Settings, run_id: str | None) -> RunManifest | None:
    """The manifest for run_id, or for the most recent run if none is given."""
    if run_id is None and _latest_file(settings).exists():
        run_id = _latest_file(settings).read_text().strip()
    if run_id is None:
        return None
    path = settings.reports_dir / run_id / "manifest.json"
    if not path.exists():
        raise typer.BadParameter(f"no manifest at {path}")
    return RunManifest.load(path)


def _reconcile(
    settings: Settings, manifest: RunManifest | None, since: datetime | None, two_way: bool, out: Path
):
    with OracleClient.from_settings(settings) as oracle:
        engine = ReconciliationEngine(Mapping.load(settings.mapping_path), settings, oracle)
        result = engine.run(
            since=manifest.read_from if manifest else since,
            run_id=manifest.run_id if manifest else None,
            two_way=two_way,
        )
    recon_report.print_summary(result, console)
    paths = recon_report.write_reports(result, out)
    console.print(f"Reports: {paths['html']}")
    return result


def _trace(settings: Settings, manifest: RunManifest | None, since: datetime | None, sla: float, out: Path):
    with OracleClient.from_settings(settings) as oracle:
        traces = collect_traces(
            settings,
            oracle,
            since=manifest.read_from if manifest else since,
            run_id=manifest.run_id if manifest else None,
            correlation_ids=manifest.correlation_ids if manifest else None,
        )
    result = SlaReport(
        sla_seconds=sla, traces=list(traces.values()), run_id=manifest.run_id if manifest else None
    )
    sla_report.print_summary(result, console)
    paths = sla_report.write_reports(result, out)
    console.print(f"Reports: {paths['html']}")
    return result


# ---------------- commands ----------------


@app.command()
def scenarios() -> None:
    """List the payload scenarios and their expected outcomes."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Scenario")
    table.add_column("Expected")
    table.add_column("Default weight", justify="right")
    table.add_column("Description")
    for name, scenario in SCENARIOS.items():
        table.add_row(name, scenario.expected_outcome, str(DEFAULT_MIX.get(name, 0)), scenario.description)
    console.print(table)


@app.command("generate")
def generate_files(
    scenario: Annotated[str, typer.Option("--scenario", "-s", help="Scenario name")] = "standard",
    count: Annotated[int, typer.Option("--count", "-n")] = 1,
    out: Annotated[Path, typer.Option("--out", "-o", help="Directory for the XML files")] = Path("samples"),
    seed: Annotated[int | None, typer.Option(help="Seed for reproducible data")] = None,
) -> None:
    """Write generated XML payloads to files (no Kafka needed)."""
    if scenario not in SCENARIOS:
        raise typer.BadParameter(f"unknown scenario {scenario}")
    out.mkdir(parents=True, exist_ok=True)
    factory = DeclarationFactory(seed)
    for _ in range(count):
        message = SCENARIOS[scenario].generate(factory)
        payload = message.payload
        if message.expected_outcome != "MALFORMED_XML":  # pretty-print what can be parsed
            payload = to_bytes(parse(payload), pretty=True)
        path = out / f"{scenario}-{message.declaration_ref}.xml"
        path.write_bytes(payload)
        console.print(f"{path}  [dim]({message.expected_outcome})[/]")


@app.command()
def publish(
    count: Annotated[int, typer.Option("--count", "-n", help="Messages to publish")] = 1000,
    mix: Annotated[
        str, typer.Option(help="'default', 'valid', or e.g. 'standard=3,unicode_text=1'")
    ] = "default",
    rate: Annotated[
        float | None, typer.Option(help="Max messages per second (default: as fast as possible)")
    ] = None,
    seed: Annotated[int | None, typer.Option(help="Seed for reproducible data")] = None,
    duplicate_every: Annotated[int, typer.Option(help="Re-send every Nth message (idempotency)")] = 0,
    wait: Annotated[bool, typer.Option(help="Wait until every message is stored or dead-lettered")] = True,
) -> None:
    """Generate a tagged run of XML declarations and publish it to the inbound topic."""
    settings = get_settings()
    messages = generate(count, _parse_mix(mix), seed=seed)
    manifest = publish_run(settings, messages, rate=rate, duplicate_every=duplicate_every)
    path = manifest.save(settings.reports_dir / manifest.run_id / "manifest.json")
    _latest_file(settings).write_text(manifest.run_id)
    console.print(
        f"Published [bold]{len(manifest.entries):,}[/] messages as run [cyan]{manifest.run_id}[/] "
        f"({len(manifest.expected_rejected)} expected to be rejected). Manifest: {path}"
    )
    if wait:
        result = wait_for_run(settings, manifest)
        state = "[green]all accounted for[/]" if result.complete else "[yellow]not all accounted for[/]"
        console.print(
            f"Pipeline: {len(result.persisted):,} stored, {len(result.rejected):,} dead-lettered, "
            f"{state} after {result.waited_seconds:.1f}s"
        )


@app.command()
def reconcile(
    run_id: Annotated[str | None, typer.Option(help="Run to reconcile (default: latest run)")] = None,
    since: Annotated[
        str | None, typer.Option(help="Reconcile a time window instead: ISO time, 'today', '2h'")
    ] = None,
    two_way: Annotated[
        bool, typer.Option(help="Also find Oracle rows with no message (needs --since)")
    ] = False,
    out: Annotated[Path | None, typer.Option(help="Report directory")] = None,
) -> None:
    """Reconcile Kafka XML messages against Oracle, field by field. Exit code 1 on discrepancies."""
    settings = get_settings()
    window = _parse_since(since)
    manifest = None if window else _resolve_run(settings, run_id)
    if manifest is None and window is None:
        raise typer.BadParameter("no run found: publish one first, or pass --since")
    out = out or settings.reports_dir / (
        manifest.run_id if manifest else f"window-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    )
    result = _reconcile(settings, manifest, window, two_way, out)
    raise typer.Exit(0 if result.passed else 1)


@app.command()
def trace(
    run_id: Annotated[str | None, typer.Option(help="Run to trace (default: latest run)")] = None,
    since: Annotated[
        str | None, typer.Option(help="Trace a time window instead: ISO time, 'today', '2h'")
    ] = None,
    sla: Annotated[float | None, typer.Option(help="SLA in seconds (default: SLA_SECONDS, 13)")] = None,
    out: Annotated[Path | None, typer.Option(help="Report directory")] = None,
) -> None:
    """Trace every correlation ID end to end and check the latency SLA. Exit code 1 on breaches."""
    settings = get_settings()
    window = _parse_since(since)
    manifest = None if window else _resolve_run(settings, run_id)
    if manifest is None and window is None:
        raise typer.BadParameter("no run found: publish one first, or pass --since")
    out = out or settings.reports_dir / (
        manifest.run_id if manifest else f"window-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    )
    result = _trace(settings, manifest, window, sla or settings.sla_seconds, out)
    raise typer.Exit(0 if result.passed else 1)


@app.command()
def timeline(
    correlation_id: Annotated[str, typer.Argument(help="Correlation ID to follow")],
    since: Annotated[str, typer.Option(help="How far back to search: ISO time, 'today', '2h'")] = "24h",
    logs: Annotated[bool, typer.Option(help="Include CloudWatch log events")] = True,
) -> None:
    """Follow one message across Kafka, the services' CloudWatch logs and Oracle."""
    settings = get_settings()
    window = _parse_since(since)
    with OracleClient.from_settings(settings) as oracle:
        traces = collect_traces(settings, oracle, since=window, correlation_ids={correlation_id})
    events = []
    if logs:
        try:
            events = find_log_events(settings, correlation_id, since=window)
        except Exception as exc:  # CloudWatch is optional
            console.print(f"[yellow]CloudWatch unavailable:[/] {exc}")
    sla_report.print_timeline(traces[correlation_id], events, settings.sla_seconds, console)


@app.command()
def run(
    count: Annotated[int, typer.Option("--count", "-n")] = 1000,
    mix: Annotated[str, typer.Option()] = "default",
    rate: Annotated[float | None, typer.Option()] = 200.0,
    seed: Annotated[int | None, typer.Option()] = None,
) -> None:
    """Publish a run, wait for the pipeline, then reconcile and trace it."""
    settings = get_settings()
    messages = generate(count, _parse_mix(mix), seed=seed)
    manifest = publish_run(settings, messages, rate=rate)
    manifest.save(settings.reports_dir / manifest.run_id / "manifest.json")
    _latest_file(settings).write_text(manifest.run_id)
    console.print(
        f"Published [bold]{count:,}[/] messages as run [cyan]{manifest.run_id}[/]; "
        "waiting for the pipeline..."
    )
    waited = wait_for_run(settings, manifest)
    console.print(f"{len(waited.accounted):,}/{count:,} accounted for after {waited.waited_seconds:.1f}s")
    out = settings.reports_dir / manifest.run_id
    reconciled = _reconcile(settings, manifest, None, False, out)
    traced = _trace(settings, manifest, None, settings.sla_seconds, out)
    raise typer.Exit(0 if reconciled.passed and traced.passed else 1)


if __name__ == "__main__":
    app()
