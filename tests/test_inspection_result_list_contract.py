# coding=utf-8
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.inspection import logic


class _FakeResultQuery:
    def __init__(self, items):
        self.items = items

    def filter(self, **_kwargs):
        return self

    async def count(self):
        return len(self.items)

    def order_by(self, *_fields):
        return self

    def only(self, *_fields):
        return self

    def offset(self, _offset):
        return self

    def limit(self, _limit):
        return self

    async def all(self):
        return self.items


class _FakeScheduleQuery:
    def __init__(self, items):
        self.items = items
        self.order_fields = None
        self.offset_value = None
        self.limit_value = None

    def filter(self, **_kwargs):
        return self

    async def count(self):
        return len(self.items)

    def order_by(self, *fields):
        self.order_fields = fields
        return self

    def offset(self, offset):
        self.offset_value = offset
        return self

    def limit(self, limit):
        self.limit_value = limit
        return self

    async def all(self):
        return self.items


class _FakeTargetQuery:
    def __init__(self, targets):
        self.targets = targets

    async def all(self):
        return self.targets


@pytest.mark.asyncio
async def test_list_results_includes_execution_status_and_result_data(monkeypatch):
    result = SimpleNamespace(
        id="result-1",
        target_id="target-1",
        schedule_id="schedule-1",
        start_time=datetime(2026, 7, 7, 3, 27, tzinfo=timezone.utc),
        end_time=None,
        status="success",
        execution_status="running",
        result_data={
            "result": {
                "status": "running",
                "ai_judgment": {"passed": None, "reason": "still running"},
            }
        },
        error_message=None,
        duration=None,
    )
    target = SimpleNamespace(
        id="target-1",
        content="获取CPU指标信息",
        standard="状态正确显示",
    )

    class FakeInspectionResult:
        @staticmethod
        def filter(**_kwargs):
            return _FakeResultQuery([result])

    class FakeInspectionTarget:
        @staticmethod
        def filter(**_kwargs):
            return _FakeTargetQuery([target])

    monkeypatch.setattr(logic, "InspectionResult", FakeInspectionResult)
    monkeypatch.setattr(logic, "InspectionTarget", FakeInspectionTarget)

    payload = await logic.list_results(page=1, page_size=20, tenant_id="tenant-a")

    item = payload["items"][0]
    assert item["execution_status"] == "running"
    assert item["result_data"] == result.result_data
    assert item["reason"] == "still running"


@pytest.mark.asyncio
async def test_list_results_prefers_immutable_target_snapshot(monkeypatch):
    result = SimpleNamespace(
        id="result-1",
        target_id="target-1",
        target_content="执行时的巡检内容",
        target_standard="执行时的巡检标准",
        schedule_id="schedule-1",
        start_time=datetime(2026, 8, 17, 3, 27, tzinfo=timezone.utc),
        end_time=None,
        status="success",
        execution_status="success",
        result_data={},
        error_message=None,
        duration=1,
    )
    target = SimpleNamespace(
        id="target-1",
        content="后来修改的巡检内容",
        standard="后来修改的巡检标准",
    )

    class FakeInspectionResult:
        @staticmethod
        def filter(**_kwargs):
            return _FakeResultQuery([result])

    class FakeInspectionTarget:
        @staticmethod
        def filter(**_kwargs):
            return _FakeTargetQuery([target])

    monkeypatch.setattr(logic, "InspectionResult", FakeInspectionResult)
    monkeypatch.setattr(logic, "InspectionTarget", FakeInspectionTarget)

    payload = await logic.list_results(page=1, page_size=20, tenant_id="tenant-a")

    assert payload["items"][0]["target_content"] == "执行时的巡检内容"
    assert payload["items"][0]["target_standard"] == "执行时的巡检标准"


@pytest.mark.asyncio
async def test_create_result_captures_authoritative_target_snapshot(monkeypatch):
    target = SimpleNamespace(
        id="target-1",
        category_id="category-1",
        content="巡检内容",
        standard="巡检标准",
    )
    category = SimpleNamespace(id="category-1", schedule_id="schedule-1")
    captured = {}

    class _FirstQuery:
        def __init__(self, value):
            self.value = value

        async def first(self):
            return self.value

    class FakeInspectionTarget:
        @staticmethod
        def filter(**_kwargs):
            return _FirstQuery(target)

    class FakeInspectionCategory:
        @staticmethod
        def filter(**_kwargs):
            return _FirstQuery(category)

    class FakeInspectionResult:
        @staticmethod
        async def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(**kwargs)

    monkeypatch.setattr(logic, "InspectionTarget", FakeInspectionTarget)
    monkeypatch.setattr(logic, "InspectionCategory", FakeInspectionCategory)
    monkeypatch.setattr(logic, "InspectionResult", FakeInspectionResult)

    await logic.create_result(
        {
            "target_id": "target-1",
            "status": "success",
            "execution_status": "success",
        },
        "tenant-a",
    )

    assert captured["schedule_id"] == "schedule-1"
    assert captured["target_content"] == "巡检内容"
    assert captured["target_standard"] == "巡检标准"
    assert captured["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_word_report_uses_immutable_target_snapshot(monkeypatch):
    result = SimpleNamespace(
        id="result-1",
        target_id="target-1",
        target_content="执行时的巡检内容",
        target_standard="执行时的巡检标准",
        status="success",
        result_data={},
        error_message=None,
    )
    target = SimpleNamespace(
        id="target-1",
        category_id="category-1",
        content="后来修改的巡检内容",
        standard="后来修改的巡检标准",
    )
    category = SimpleNamespace(
        id="category-1",
        name="系统巡检",
        app_name="基础设施",
    )
    rendered = {}

    class FakeInspectionResult:
        @staticmethod
        def filter(**_kwargs):
            return _FakeResultQuery([result])

    class FakeInspectionTarget:
        @staticmethod
        def filter(**_kwargs):
            return _FakeTargetQuery([target])

    class FakeInspectionCategory:
        @staticmethod
        def filter(**_kwargs):
            return _FakeTargetQuery([category])

    class FakeDocxTemplate:
        def __init__(self, _template_path):
            pass

        def render(self, context):
            rendered.update(context)

        def save(self, buffer):
            buffer.write(b"report")

    async def fake_upload(*_args, **_kwargs):
        return "report.docx"

    monkeypatch.setattr(logic, "InspectionResult", FakeInspectionResult)
    monkeypatch.setattr(logic, "InspectionTarget", FakeInspectionTarget)
    monkeypatch.setattr(logic, "InspectionCategory", FakeInspectionCategory)
    monkeypatch.setattr(logic, "DocxTemplate", FakeDocxTemplate)
    monkeypatch.setattr(logic.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(logic, "upload_to_s3", fake_upload)

    report_url = await logic.generate_and_upload_inspection_word(
        "schedule-1",
        [{"id": "result-1"}],
        "tenant-a",
    )

    assert report_url == "report.docx"
    assert rendered["targets"] == [{"content": "执行时的巡检内容"}]
    assert rendered["results"][0]["content"] == "执行时的巡检内容"


@pytest.mark.asyncio
async def test_list_schedules_orders_enabled_and_newest_first(monkeypatch):
    schedule = SimpleNamespace(
        id="schedule-1",
        name="test",
        schedule_cron="0 * * * *",
        owner=None,
        enabled=True,
        description="",
        ctime=datetime(2026, 7, 8, 1, 0, tzinfo=timezone.utc),
        utime=datetime(2026, 7, 8, 2, 0, tzinfo=timezone.utc),
        notify_enabled=False,
        notify_config=None,
        custom_report_config=None,
    )
    schedule_query = _FakeScheduleQuery([schedule])

    class FakeInspectionSchedule:
        @staticmethod
        def filter(**_kwargs):
            return schedule_query

    class FakeInspectionResult:
        @staticmethod
        def filter(**_kwargs):
            return _FakeResultQuery([])

    monkeypatch.setattr(logic, "InspectionSchedule", FakeInspectionSchedule)
    monkeypatch.setattr(logic, "InspectionResult", FakeInspectionResult)

    payload = await logic.list_schedules(page=2, page_size=15, tenant_id="tenant-a")

    assert schedule_query.order_fields == ("-enabled", "-utime", "-ctime")
    assert schedule_query.offset_value == 15
    assert schedule_query.limit_value == 15
    assert payload["items"][0]["id"] == "schedule-1"
