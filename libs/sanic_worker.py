# coding=utf-8

import os

from sanic.worker.manager import WorkerManager
from sanic.worker.process import WorkerProcess

DEFAULT_WORKER_ACK_TIMEOUT_SECONDS = 120
MIN_WORKER_ACK_TIMEOUT_SECONDS = 30


def configure_worker_ack_timeout(raw_timeout: str | None = None) -> int:
    """Configure Sanic's worker ack timeout and return the effective seconds."""
    raw_timeout = raw_timeout if raw_timeout is not None else os.getenv("SANIC_WORKER_ACK_TIMEOUT_SECONDS")
    try:
        timeout_seconds = int(raw_timeout) if raw_timeout is not None else DEFAULT_WORKER_ACK_TIMEOUT_SECONDS
    except ValueError:
        timeout_seconds = DEFAULT_WORKER_ACK_TIMEOUT_SECONDS

    timeout_seconds = max(timeout_seconds, MIN_WORKER_ACK_TIMEOUT_SECONDS)
    threshold = timeout_seconds * 10
    WorkerProcess.THRESHOLD = threshold
    WorkerManager.THRESHOLD = threshold
    return timeout_seconds
