from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = (ROOT / "services" / "network_access_control" / "manager.py").read_text()


def _disposal_task_config_source() -> str:
    start = MANAGER.index('"disposal-task": {')
    end = MANAGER.index('"attachment-ref": {', start)
    return MANAGER[start:end]


def test_disposal_task_generic_save_keeps_execution_fields_read_only():
    config = _disposal_task_config_source()

    for field in ["status", "confirm_by", "confirmed_at", "run_id", "result_message"]:
        assert f'"{field}",' not in config

    assert '"status": DISPOSAL_TASK_STATUSES' not in config
    assert (
        'if domain == "disposal-task":\n        payload["status"] = "pending_confirm"'
        in MANAGER
    )
    assert (
        'if domain == "disposal-task":\n            payload.pop("status", None)'
        in MANAGER
    )


def test_disposal_task_execution_paths_remain_dedicated():
    assert "async def confirm_disposal_task" in MANAGER
    assert 'task.status = "executing"' in MANAGER
    assert '"execution_payload": {' in MANAGER
    assert "async def cancel_disposal_task" in MANAGER
