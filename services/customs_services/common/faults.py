"""Deliberate defects that can be switched on to prove the harness catches them.

Set FAULTS on a service to a comma-separated list, for example:

    PERSISTENCE_FAULTS="rounding,truncate_description,drop=0.02" make up
    ASSESSMENT_FAULTS="delay=0.05:15000,lane_boundary" make up

Rate-based faults are deterministic per declaration reference, so a re-run
with the same data hits the same messages.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CATALOGUE = {
    # persistence
    "rounding": "TotalGrossMassKg rounded to 2dp instead of 3dp (column precision bug)",
    "truncate_description": "Goods descriptions truncated to 100 characters",
    "ascii_names": "Non-ASCII characters stripped from party names (character-set bug)",
    "drop_millis": "Fractional seconds dropped from MessageCreatedAt (bound as DATE)",
    "skip_last_item": "Last goods item lost on multi-item declarations (off-by-one)",
    "drop": "drop=<rate>: a fraction of declarations are acknowledged but never stored",
    # assessment
    "lane_boundary": "Uses > instead of >= at the red-lane threshold (boundary bug)",
    "delay": "delay=<rate>:<ms>: a fraction of messages are held up (SLA breach)",
}


@dataclass(frozen=True)
class Faults:
    flags: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, spec: str) -> Faults:
        flags: dict[str, str] = {}
        for part in filter(None, (p.strip() for p in spec.split(","))):
            name, _, value = part.partition("=")
            if name not in CATALOGUE:
                raise ValueError(f"unknown fault '{name}'; known: {', '.join(CATALOGUE)}")
            flags[name] = value
        return cls(flags)

    def enabled(self, name: str) -> bool:
        return name in self.flags

    def hits(self, name: str, key: str) -> bool:
        """True if rate-based fault `name` selects this key."""
        if name not in self.flags:
            return False
        rate = float(self.flags[name].split(":")[0] or "1")
        digest = hashlib.sha256(f"{name}:{key}".encode()).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF < rate

    def delay_ms(self) -> int:
        _, _, ms = self.flags.get("delay", "").partition(":")
        return int(ms or 15000)

    def __bool__(self) -> bool:
        return bool(self.flags)
