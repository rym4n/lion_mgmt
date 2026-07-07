# coding=utf-8

import math
import os

DEFAULT_WORKER_ACK_TIMEOUT_SECONDS = 120
MIN_WORKER_ACK_TIMEOUT_SECONDS = 30
DEFAULT_HEALTH_MAX_MISSES = 3
SANIC_WORKER_ACK_TIMEOUT_ENV = "SANIC_WORKER_ACK_TIMEOUT_SECONDS"
SANIC_HEALTH_MAX_MISSES_ENV = "SANIC_HEALTH_MAX_MISSES"


def _coerce_int(raw_value: str | None, default: int) -> int:
    try:
        return int(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        return default


def resolve_worker_ack_timeout(raw_timeout: str | None = None) -> int:
    raw_timeout = raw_timeout if raw_timeout is not None else os.getenv(SANIC_WORKER_ACK_TIMEOUT_ENV)
    timeout_seconds = _coerce_int(raw_timeout, DEFAULT_WORKER_ACK_TIMEOUT_SECONDS)
    return max(timeout_seconds, MIN_WORKER_ACK_TIMEOUT_SECONDS)


def resolve_health_max_misses(raw_max_misses: str | None = None) -> int:
    raw_max_misses = raw_max_misses if raw_max_misses is not None else os.getenv(SANIC_HEALTH_MAX_MISSES_ENV)
    return max(_coerce_int(raw_max_misses, DEFAULT_HEALTH_MAX_MISSES), 1)


def resolve_health_missed_threshold(timeout_seconds: int | None = None, max_misses: int | None = None) -> int:
    timeout_seconds = timeout_seconds if timeout_seconds is not None else resolve_worker_ack_timeout()
    max_misses = max_misses if max_misses is not None else resolve_health_max_misses()
    return max(math.ceil(timeout_seconds / max(max_misses, 1)), 1)


def configure_worker_ack_timeout(raw_timeout: str | None = None) -> int:
    """Configure Sanic's worker ack timeout and return the effective seconds."""
    timeout_seconds = resolve_worker_ack_timeout(raw_timeout)
    os.environ[SANIC_WORKER_ACK_TIMEOUT_ENV] = str(timeout_seconds)
    threshold = timeout_seconds * 10

    from sanic.worker.manager import WorkerManager
    from sanic.worker.process import WorkerProcess

    WorkerProcess.THRESHOLD = threshold
    WorkerManager.THRESHOLD = threshold
    return timeout_seconds
