"""File-based liveness heartbeat for the container healthcheck.

A service only beats once its consumer has partitions assigned, so
`docker compose up --wait` returns when the pipeline can actually take traffic.

    python -m customs_services.common.heartbeat --check   # exit 0 if alive
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HEARTBEAT_FILE = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))
MAX_AGE_SECONDS = 15.0


class Heartbeat:
    def __init__(self, path: Path = HEARTBEAT_FILE, every_seconds: float = 1.0) -> None:
        self.path = path
        self.every_seconds = every_seconds
        self._last = 0.0

    def beat(self) -> None:
        now = time.monotonic()
        if now - self._last >= self.every_seconds:
            self.path.touch()
            self._last = now


def is_alive(path: Path = HEARTBEAT_FILE) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_SECONDS


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if is_alive() else 1)
    print(__doc__)
