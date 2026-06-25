# coding=utf-8
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


employee_report_payload = _load_module("employee_report_payload", "services/digital_employee/employee_report_payload.py")
compose_payload = employee_report_payload.compose_payload


def test_compose_payload_for_completed_task():
    task = SimpleNamespace(title="告警处理", status="completed", result="已确认误报", description="处理 CPU 告警")

    payload = compose_payload(task, [{"description": "查日志", "status": "completed"}])

    assert payload["severity"] == "info"
    assert payload["conclusion"] == "已确认误报"
    assert payload["actions"][0]["description"] == "查日志"


def test_compose_payload_for_failed_task():
    task = SimpleNamespace(title="告警处理", status="failed", result=None, description="处理 CPU 告警")

    payload = compose_payload(task)

    assert payload["severity"] == "high"
    assert "失败" in payload["observation"]
