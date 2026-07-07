import os
import subprocess
import sys

from libs.sanic_worker import (
    SANIC_WORKER_ACK_TIMEOUT_ENV,
    configure_worker_ack_timeout,
    resolve_health_max_misses,
    resolve_health_missed_threshold,
    resolve_worker_ack_timeout,
)


def test_configure_worker_ack_timeout_uses_default_for_invalid_value(monkeypatch):
    from sanic.worker.manager import WorkerManager
    from sanic.worker.process import WorkerProcess

    monkeypatch.delenv(SANIC_WORKER_ACK_TIMEOUT_ENV, raising=False)

    assert configure_worker_ack_timeout("bad-value") == 120
    assert WorkerProcess.THRESHOLD == 1200
    assert WorkerManager.THRESHOLD == 1200
    assert os.environ[SANIC_WORKER_ACK_TIMEOUT_ENV] == "120"


def test_configure_worker_ack_timeout_enforces_minimum():
    from sanic.worker.manager import WorkerManager
    from sanic.worker.process import WorkerProcess

    assert configure_worker_ack_timeout("5") == 30
    assert WorkerProcess.THRESHOLD == 300
    assert WorkerManager.THRESHOLD == 300


def test_configure_worker_ack_timeout_accepts_env_value(monkeypatch):
    monkeypatch.setenv("SANIC_WORKER_ACK_TIMEOUT_SECONDS", "45")

    assert configure_worker_ack_timeout() == 45
    from sanic.worker.manager import WorkerManager
    from sanic.worker.process import WorkerProcess

    assert WorkerProcess.THRESHOLD == 450
    assert WorkerManager.THRESHOLD == 450


def test_resolve_worker_ack_timeout_reads_env(monkeypatch):
    monkeypatch.setenv(SANIC_WORKER_ACK_TIMEOUT_ENV, "75")

    assert resolve_worker_ack_timeout() == 75


def test_resolve_health_monitor_threshold_uses_ack_window():
    assert resolve_health_missed_threshold(timeout_seconds=120, max_misses=3) == 40
    assert resolve_health_missed_threshold(timeout_seconds=45, max_misses=3) == 15


def test_resolve_health_max_misses_enforces_positive_value():
    assert resolve_health_max_misses("0") == 1


def test_sanic_worker_config_does_not_import_sanic_worker_modules_early():
    code = (
        "import sys; "
        "import libs.sanic_worker; "
        "print('sanic.worker.manager' in sys.modules); "
        "print('sanic.worker.process' in sys.modules)"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert result.stdout.splitlines() == ["False", "False"]
