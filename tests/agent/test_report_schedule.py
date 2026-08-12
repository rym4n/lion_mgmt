# coding=utf-8
import asyncio
import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytz


def _load_schedule_runner():
    for name in [
        "services.agent.schedule_runner",
        "services.agent",
        "services",
        "models",
        "models.report",
        "libs.kafka",
        "settings",
        "croniter",
    ]:
        sys.modules.pop(name, None)

    services_module = types.ModuleType("services")
    services_module.__path__ = []
    sys.modules["services"] = services_module
    agent_pkg = types.ModuleType("services.agent")
    agent_pkg.__path__ = []
    sys.modules["services.agent"] = agent_pkg

    libs_module = types.ModuleType("libs")
    libs_module.__path__ = []
    sys.modules["libs"] = libs_module
    kafka_module = types.ModuleType("libs.kafka")
    kafka_module.published = []

    async def aproduce(topic, value):
        kafka_module.published.append((topic, value))

    kafka_module.aproduce = aproduce
    sys.modules["libs.kafka"] = kafka_module

    settings_module = types.ModuleType("settings")
    settings_module.TASK_TOPIC = "topic"
    settings_module.TZ = pytz.timezone("Asia/Shanghai")
    sys.modules["settings"] = settings_module

    croniter_module = types.ModuleType("croniter")

    class FakeCroniter:
        def __init__(self, expr, base):
            self.expr = expr
            self.base = base

        @staticmethod
        def is_valid(expr):
            return expr == "*/15 * * * *" or expr == "*/5 * * * *"

        def get_next(self, cls):
            if self.expr == "*/15 * * * *":
                return self.base.replace(minute=15, second=0, microsecond=0)
            return self.base.replace(minute=5, second=0, microsecond=0)

    croniter_module.croniter = FakeCroniter
    sys.modules["croniter"] = croniter_module

    class FakeTimed:
        created = []

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.id = kwargs.get("id", "timed-1")
            self.deleted = kwargs.get("deleted", False)

        @classmethod
        async def create(cls, **kwargs):
            timed = cls(**kwargs)
            cls.created.append(timed)
            return timed

        @classmethod
        def filter(cls, **kwargs):
            return SimpleNamespace(first=lambda: _none())

        @classmethod
        async def get_or_none(cls, **kwargs):
            return None

        async def save(self, *args, **kwargs):
            return None

    async def _none():
        return None

    models_module = types.ModuleType("models")
    models_module.Account = type("Account", (), {})
    models_module.Timed = FakeTimed
    sys.modules["models"] = models_module

    report_module = types.ModuleType("models.report")
    report_module.ReportDocument = type("ReportDocument", (), {})
    report_module.ReportSchedule = type("ReportSchedule", (), {})
    sys.modules["models.report"] = report_module

    path = Path(__file__).resolve().parents[2] / "services/agent/schedule_runner.py"
    spec = importlib.util.spec_from_file_location("services.agent.schedule_runner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["services.agent.schedule_runner"] = module
    spec.loader.exec_module(module)
    return module, FakeTimed, kafka_module


def test_calculate_next_run_at_uses_croniter():
    runner, _, _ = _load_schedule_runner()
    base = pytz.timezone("Asia/Shanghai").localize(datetime(2026, 6, 28, 2, 0, 0))

    assert runner.calculate_next_run_at("*/15 * * * *", base) == pytz.timezone("Asia/Shanghai").localize(
        datetime(2026, 6, 28, 2, 15, 0)
    )


def test_build_report_timer_message_shape_uses_action_bridge():
    runner, _, _ = _load_schedule_runner()
    schedule = SimpleNamespace(
        id="schedule-1",
        document_id="doc-1",
        tenant_id="tenant-1",
        creator_id="user-1",
        name="日报",
        cron="*/5 * * * *",
        export_format="pdf",
    )
    timed = SimpleNamespace(id="timed-1", end_time="2099-12-31 23:59:59")

    message = runner.build_report_timer_message(schedule, timed)

    assert message["run_type"] == "action"
    assert message["timer"] == "cron"
    assert message["timer_rule"]["crontab"] == "*/5 * * * *"
    assert message["run_args"]["action_input"]["schedule_id"] == "schedule-1"
    assert message["run_args"]["action_input"]["tenant_id"] == "tenant-1"
    assert message["run_args"]["action_input"]["timeout"] == 600
    assert message["run_args"]["app_name"] == "digital_employee"
    assert message["run_args"]["app_ver"] == "v1"
    assert message["run_args"]["main"] == "worker.py"
    assert message["run_args"]["action"] == "run_report_schedule"
    assert message["run_args"]["executor"] == "user-1"


def test_disabled_schedule_creates_timed_row_without_publishing_timer():
    runner, fake_timed, kafka_module = _load_schedule_runner()
    schedule = SimpleNamespace(
        id="schedule-1",
        tenant_id="tenant-1",
        document_id="doc-1",
        creator_id="user-1",
        timed_id=None,
        name="日报",
        cron="*/5 * * * *",
        export_format="docx",
        enabled=False,
        saved=[],
    )

    async def save(**kwargs):
        schedule.saved.append(kwargs)

    schedule.save = save
    operator = SimpleNamespace(id="user-1")

    result = asyncio.run(runner.sync_report_schedule_timer(schedule, operator))

    assert len(fake_timed.created) == 1
    assert fake_timed.created[0].enable == 0
    assert schedule.next_run_at is None
    assert result["published"] is False
    assert kafka_module.published == []


def test_report_schedule_validation_helpers_reject_invalid_cron_and_bool():
    views_path = Path(__file__).resolve().parents[2] / "services/agent/views.py"
    source = views_path.read_text()

    assert "croniter.is_valid(cron_expr)" in source
    assert "REPORT_SCHEDULE_CRON_INVALID" in source
    assert "def _parse_report_schedule_bool" in source
    assert "LION_ACTION_INTERNAL_TOKEN" in source
    assert "raise CustomError(ErrorCode.PARAM_ERROR)" in source


def test_report_schedule_routes_use_canonical_permissions():
    views_path = Path(__file__).resolve().parents[2] / "services/agent/views.py"
    source = views_path.read_text()
    schedule_routes = source[source.index('@agent_bp.post("/report/schedule/list")') :]

    assert '@requires_permission("agent_report_schedule:r")' in schedule_routes
    assert (
        '@requires_permission("agent_report_schedule:c", "agent_report_schedule:u")'
        in schedule_routes
    )
    assert 'check_permissions(request, "agent_report_schedule:c")' in schedule_routes
    assert 'check_permissions(request, "agent_report_schedule:u")' in schedule_routes
    assert '@requires_permission("agent_report_schedule:d")' in schedule_routes
    assert '"agent:report:schedule:' not in schedule_routes
