"""End-to-end fixtures. These tests need the docker compose stack (`make up`).

Locally they skip if Kafka/Oracle are unreachable; in CI set E2E_REQUIRED=1
so a missing stack fails the build instead.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from customs_harness.config import Settings
from customs_harness.generator.runs import RunManifest, WaitResult, publish_run, wait_for_run
from customs_harness.generator.scenarios import GeneratedMessage, generate
from customs_harness.oracle import OracleClient
from customs_harness.reconciliation.engine import ReconciliationEngine, ReconciliationReport
from customs_harness.reconciliation.mapping import Mapping


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(pytest.mark.e2e)


def _reachable(host_port: str) -> bool:
    host, _, port = host_port.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session", autouse=True)
def stack(settings: Settings) -> None:
    endpoints = {
        "Kafka": settings.kafka_bootstrap_servers.split(",")[0],
        "Oracle": settings.oracle_dsn.split("/")[0],
    }
    down = [name for name, endpoint in endpoints.items() if not _reachable(endpoint)]
    if down:
        message = f"{' and '.join(down)} not reachable - start the stack with `make up`"
        if os.environ.get("E2E_REQUIRED") == "1":
            pytest.fail(message)
        pytest.skip(message)


@pytest.fixture(scope="session")
def oracle(settings: Settings) -> OracleClient:
    with OracleClient.from_settings(settings) as client:
        yield client


@dataclass
class PipelineRun:
    manifest: RunManifest
    wait: WaitResult
    messages: list[GeneratedMessage]

    @property
    def scenario_of(self) -> dict[str, str]:
        return {m.correlation_id: m.scenario for m in self.messages}

    @property
    def run_id(self) -> str:
        return self.manifest.run_id


@pytest.fixture(scope="session")
def run_pipeline(settings: Settings) -> Callable[..., PipelineRun]:
    """Publish a tagged run and wait until every message is stored or dead-lettered."""

    def _run(
        count: int = 0,
        mix: dict[str, int] | None = None,
        messages: list[GeneratedMessage] | None = None,
        rate: float | None = None,
        duplicate_every: int = 0,
    ) -> PipelineRun:
        messages = messages or generate(count, mix)
        manifest = publish_run(settings, messages, rate=rate, duplicate_every=duplicate_every)
        manifest.save(settings.reports_dir / manifest.run_id / "manifest.json")
        return PipelineRun(manifest, wait_for_run(settings, manifest), messages)

    return _run


@pytest.fixture(scope="session")
def reconcile(settings: Settings, oracle: OracleClient) -> Callable[..., ReconciliationReport]:
    engine = ReconciliationEngine(Mapping.load(settings.mapping_path), settings, oracle)

    def _reconcile(run: PipelineRun, **kwargs) -> ReconciliationReport:
        if kwargs.get("two_way"):
            return engine.run(since=run.manifest.read_from, two_way=True)
        return engine.run(since=run.manifest.read_from, run_id=run.run_id)

    return _reconcile
