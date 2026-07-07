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

    def offset(self, _offset):
        return self

    def limit(self, _limit):
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
