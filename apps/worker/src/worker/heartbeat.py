"""File-based liveness heartbeat for the worker container.

The worker touches a file at a fixed interval; the container health check runs
`python -m worker.heartbeat`, which exits non-zero when the file is missing or stale.
"""

import os
import sys
import time
from pathlib import Path

DEFAULT_HEARTBEAT_FILE = Path("/tmp/worker-heartbeat")  # noqa: S108 - container-local liveness file
HEARTBEAT_INTERVAL_SECONDS = 5.0
MAX_HEARTBEAT_AGE_SECONDS = 30.0


def heartbeat_file() -> Path:
    return Path(os.environ.get("WORKER_HEARTBEAT_FILE", str(DEFAULT_HEARTBEAT_FILE)))


def beat(path: Path) -> None:
    path.touch()


def is_fresh(path: Path, max_age_seconds: float, now: float) -> bool:
    try:
        last_beat = path.stat().st_mtime
    except FileNotFoundError:
        return False
    return now - last_beat <= max_age_seconds


def main() -> int:
    healthy = is_fresh(heartbeat_file(), MAX_HEARTBEAT_AGE_SECONDS, time.time())
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
