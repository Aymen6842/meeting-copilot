import os
import threading
from pathlib import Path

import pytest

from worker.__main__ import run
from worker.heartbeat import beat, is_fresh, main


def test_missing_heartbeat_file_is_not_fresh(tmp_path: Path) -> None:
    assert not is_fresh(tmp_path / "missing", max_age_seconds=30, now=1_000.0)


def test_recent_heartbeat_is_fresh(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    beat(path)
    os.utime(path, (1_000.0, 1_000.0))

    assert is_fresh(path, max_age_seconds=30, now=1_030.0)


def test_stale_heartbeat_is_not_fresh(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    beat(path)
    os.utime(path, (1_000.0, 1_000.0))

    assert not is_fresh(path, max_age_seconds=30, now=1_030.5)


def test_health_check_exit_code_follows_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    monkeypatch.setenv("WORKER_HEARTBEAT_FILE", str(path))

    assert main() == 1
    beat(path)
    assert main() == 0


def test_run_returns_at_once_when_already_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    monkeypatch.setenv("WORKER_HEARTBEAT_FILE", str(path))
    stop = threading.Event()
    stop.set()

    run(stop)

    assert not path.exists()  # already stopped: the loop body never ran


def test_run_writes_heartbeat_while_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "heartbeat"
    monkeypatch.setenv("WORKER_HEARTBEAT_FILE", str(path))
    stop = threading.Event()
    worker_thread = threading.Thread(target=run, args=(stop,))

    worker_thread.start()
    try:
        for _ in range(100):
            if path.exists():
                break
            threading.Event().wait(0.01)
    finally:
        stop.set()
        worker_thread.join(timeout=5)

    assert path.exists()
    assert not worker_thread.is_alive()
