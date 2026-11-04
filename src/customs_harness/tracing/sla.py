"""SLA evaluation: percentiles, breaches, lost messages and the bottleneck stage."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from customs_harness.tracing.collector import HOPS, Trace

HISTOGRAM_BUCKETS_MS = (100, 250, 500, 1_000, 2_000, 5_000, 13_000)


def percentile(sorted_values: list[float], pct: float) -> float:
    """Linear interpolation between closest ranks (same as numpy's default)."""
    if not sorted_values:
        return math.nan
    rank = (len(sorted_values) - 1) * pct / 100
    low, high = math.floor(rank), math.ceil(rank)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (rank - low)


@dataclass(frozen=True)
class LatencyStats:
    count: int
    p50: float
    p90: float
    p95: float
    p99: float
    max: float
    mean: float

    @classmethod
    def of(cls, values: list[float]) -> LatencyStats:
        ordered = sorted(values)
        if not ordered:
            return cls(0, *(math.nan,) * 6)
        return cls(
            count=len(ordered),
            p50=percentile(ordered, 50),
            p90=percentile(ordered, 90),
            p95=percentile(ordered, 95),
            p99=percentile(ordered, 99),
            max=ordered[-1],
            mean=sum(ordered) / len(ordered),
        )


@dataclass
class SlaReport:
    sla_seconds: float
    traces: list[Trace]
    run_id: str | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def sla_ms(self) -> float:
        return self.sla_seconds * 1000

    @property
    def completed(self) -> list[Trace]:
        return [t for t in self.traces if t.end_to_end_ms is not None]

    @property
    def outcomes(self) -> Counter:
        return Counter(t.outcome for t in self.traces)

    @property
    def end_to_end(self) -> LatencyStats:
        return LatencyStats.of([t.end_to_end_ms for t in self.completed])

    @property
    def hops(self) -> dict[str, LatencyStats]:
        return {
            hop: LatencyStats.of([t.hop_ms[hop] for t in self.traces if hop in t.hop_ms])
            for hop, _, _ in HOPS
        }

    @property
    def breaches(self) -> list[Trace]:
        return sorted(
            (t for t in self.completed if t.end_to_end_ms > self.sla_ms),
            key=lambda t: t.end_to_end_ms,
            reverse=True,
        )

    @property
    def incomplete(self) -> list[Trace]:
        return [t for t in self.traces if t.outcome == "INCOMPLETE"]

    @property
    def stalled_after(self) -> Counter:
        """Where lost messages stopped: isolates the failing service."""
        return Counter(t.last_stage for t in self.incomplete)

    @property
    def breach_causes(self) -> Counter:
        """For each breach, the stage that took longest."""
        return Counter(t.slowest_hop for t in self.breaches if t.slowest_hop)

    @property
    def bottleneck(self) -> str | None:
        """Stage with the highest p95 latency."""
        stats = {hop: s for hop, s in self.hops.items() if s.count}
        return max(stats, key=lambda hop: stats[hop].p95) if stats else None

    @property
    def histogram(self) -> list[tuple[str, int]]:
        values = [t.end_to_end_ms for t in self.completed]
        edges = (0, *HISTOGRAM_BUCKETS_MS)
        buckets = []
        for low, high in zip(edges, HISTOGRAM_BUCKETS_MS, strict=False):
            buckets.append((f"{_fmt(low)}–{_fmt(high)}", sum(low <= v < high for v in values)))
        buckets.append(
            (f"> {_fmt(HISTOGRAM_BUCKETS_MS[-1])}", sum(v >= HISTOGRAM_BUCKETS_MS[-1] for v in values))
        )
        return buckets

    @property
    def anomalies(self) -> list[Trace]:
        """Traces whose timings can't be trusted (e.g. a row older than the message)."""
        return [t for t in self.traces if t.anomalies]

    @property
    def passed(self) -> bool:
        return bool(self.traces) and not self.breaches and not self.incomplete and not self.anomalies


def _fmt(ms: float) -> str:
    return f"{ms / 1000:g}s" if ms >= 1000 else f"{ms:g}ms"
