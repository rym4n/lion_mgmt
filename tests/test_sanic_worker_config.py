from sanic.worker.manager import WorkerManager
from sanic.worker.process import WorkerProcess

from libs.sanic_worker import configure_worker_ack_timeout


def test_configure_worker_ack_timeout_uses_default_for_invalid_value():
    assert configure_worker_ack_timeout("bad-value") == 120
    assert WorkerProcess.THRESHOLD == 1200
    assert WorkerManager.THRESHOLD == 1200


def test_configure_worker_ack_timeout_enforces_minimum():
    assert configure_worker_ack_timeout("5") == 30
    assert WorkerProcess.THRESHOLD == 300
    assert WorkerManager.THRESHOLD == 300


def test_configure_worker_ack_timeout_accepts_env_value(monkeypatch):
    monkeypatch.setenv("SANIC_WORKER_ACK_TIMEOUT_SECONDS", "45")

    assert configure_worker_ack_timeout() == 45
    assert WorkerProcess.THRESHOLD == 450
    assert WorkerManager.THRESHOLD == 450
