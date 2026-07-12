# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CustomError(Exception):
    def __init__(self, enum):
        self.enum = enum


class ErrorCode:
    NOT_FOUND = "NOT_FOUND"


class AwaitableValue:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def _resolve():
            return self.value

        return _resolve().__await__()


class FakeTask(SimpleNamespace):
    objects = {}

    @classmethod
    async def get_or_none(cls, id=None, tenant_id=None, deleted=False):
        task = cls.objects.get((tenant_id, id))
        if task and getattr(task, "deleted", False) == deleted:
            return task
        return None


class FakeEvent(SimpleNamespace):
    records = []

    @classmethod
    async def create(cls, **kwargs):
        kwargs.setdefault("deleted", False)
        event = cls(**kwargs)
        cls.records.append(event)
        return event

    @classmethod
    def filter(cls, **kwargs):
        class Query:
            def order_by(self, *args):
                return self

            async def first(self):
                for event in reversed(cls.records):
                    if all(getattr(event, key) == value for key, value in kwargs.items()):
                        return event
                return None

        return Query()


class FakeReport(SimpleNamespace):
    records = {}

    @property
    def json(self):
        return {
            "id": self.id,
            "task_id": self.task.id,
            "role_key": self.role_key,
            "title": self.title,
            "severity": self.severity,
            "actions": self.actions,
            "conclusion": self.conclusion,
        }

    @classmethod
    async def get_or_create(cls, tenant_id=None, task=None, defaults=None):
        key = (tenant_id, task.id)
        if key in cls.records:
            return cls.records[key], False
        report = cls(id=f"report-{task.id}", tenant_id=tenant_id, task=task, **(defaults or {}))
        cls.records[key] = report
        return report, True

    async def save(self):
        self.saved = True


def _install_stubs():
    employee = SimpleNamespace(id="employee-1", role_key="monitor")
    task = FakeTask(
        id="task-1",
        tenant_id="tenant-1",
        title="处理告警",
        description="检查并处理 CPU 告警",
        status="running",
        progress=50,
        run_id="run-1",
        assigned_agent_key="monitor-1",
        assigned_agent=AwaitableValue(employee),
        deleted=False,
    )
    FakeTask.objects = {("tenant-1", "task-1"): task}
    FakeEvent.records = []
    FakeReport.records = {}

    sys.modules.setdefault("core", types.ModuleType("core"))
    core_error_code = types.ModuleType("core.error_code")
    core_error_code.ErrorCode = ErrorCode
    sys.modules["core.error_code"] = core_error_code

    sys.modules.setdefault("libs", types.ModuleType("libs"))
    sys.modules.setdefault("libs.error_code", types.ModuleType("libs.error_code"))
    exception_module = types.ModuleType("libs.error_code.exception")
    exception_module.CustomError = CustomError
    sys.modules["libs.error_code.exception"] = exception_module

    sys.modules.setdefault("models", types.ModuleType("models"))
    command_center = types.ModuleType("models.command_center")
    command_center.CommandTask = FakeTask
    command_center.CommandTaskEvent = FakeEvent
    sys.modules["models.command_center"] = command_center
    employee_report = types.ModuleType("models.employee_report")
    employee_report.EmployeeReport = FakeReport
    sys.modules["models.employee_report"] = employee_report


def test_save_and_fetch_runtime_transcript_round_trips_latest_actions():
    _install_stubs()
    module = _load_module(
        "services.digital_employee.runtime_callbacks",
        "services/digital_employee/runtime_callbacks.py",
    )

    saved = asyncio.run(
        module.save_runtime_transcript(
            "tenant-1",
            {
                "task_id": "task-1",
                "run_id": "run-2",
                "actions_draft": [{"description": "查日志", "status": "completed"}],
                "transcript": [{"role": "assistant", "content": "done"}],
                "content": "已处理",
            },
        )
    )
    fetched = asyncio.run(module.fetch_runtime_transcript("tenant-1", "task-1"))

    assert saved["run_id"] == "run-2"
    assert fetched["actions"] == [{"description": "查日志", "status": "completed"}]
    assert fetched["actions_draft"] == fetched["actions"]
    assert fetched["transcript"] == [{"role": "assistant", "content": "done"}]
    assert FakeEvent.records[0].event_type == module.TRANSCRIPT_EVENT_TYPE
    assert FakeEvent.records[0].agent_key == "monitor-1"


def test_save_runtime_report_upserts_employee_report():
    _install_stubs()
    module = _load_module(
        "services.digital_employee.runtime_callbacks",
        "services/digital_employee/runtime_callbacks.py",
    )

    created = asyncio.run(
        module.save_runtime_report(
            "tenant-1",
            {
                "task_id": "task-1",
                "report": {
                    "title": "告警报告",
                    "severity": "high",
                    "actions": [{"description": "查日志"}],
                    "conclusion": "需要处理",
                },
            },
        )
    )
    updated = asyncio.run(
        module.save_runtime_report(
            "tenant-1",
            {
                "task_id": "task-1",
                "report": {"title": "告警报告", "severity": "info", "conclusion": "已恢复"},
            },
        )
    )

    assert created is updated
    assert updated.role_key == "monitor"
    assert updated.severity == "info"
    assert updated.conclusion == "已恢复"
    assert updated.saved is True


def test_save_runtime_report_defaults_title_to_task_content():
    _install_stubs()
    module = _load_module(
        "services.digital_employee.runtime_callbacks",
        "services/digital_employee/runtime_callbacks.py",
    )

    report = asyncio.run(
        module.save_runtime_report(
            "tenant-1",
            {
                "task_id": "task-1",
                "report": {"severity": "info", "conclusion": "已恢复"},
            },
        )
    )

    assert report.title == "检查并处理 CPU 告警"
