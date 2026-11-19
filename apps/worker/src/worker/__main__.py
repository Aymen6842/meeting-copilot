"""Placeholder worker process: keeps a heartbeat until stopped.

Task 8 replaces this loop with the arq worker; the heartbeat health check stays.
"""

import logging
import signal
import threading
from types import FrameType

from worker.heartbeat import HEARTBEAT_INTERVAL_SECONDS, beat, heartbeat_file

logger = logging.getLogger("worker")


def run(stop: threading.Event) -> None:
    path = heartbeat_file()
    logger.info("worker started; no jobs are processed yet")
    while not stop.is_set():
        beat(path)
        stop.wait(HEARTBEAT_INTERVAL_SECONDS)
    logger.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()

    def request_stop(signal_number: int, frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run(stop)


if __name__ == "__main__":
    main()
