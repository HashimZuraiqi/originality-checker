"""Standalone worker process: `python -m app.worker`."""

import logging
import signal
import threading

from ..config import get_settings
from ..db import create_all
from .queue import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    create_all()
    worker = Worker(get_settings().worker_threads)
    worker.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    worker.stop()


if __name__ == "__main__":
    main()
