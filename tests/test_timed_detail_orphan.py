# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.timed import views as timed_views


class _AwaitableTimedQuery:
    def __init__(self, timed):
        self.timed = timed

    def prefetch_related(self, *_relations):
        return self

    def __await__(self):
        async def resolve():
            return self.timed

        return resolve().__await__()


def test_timed_detail_keeps_base_fields_when_playbook_version_is_missing(monkeypatch):
    timed = SimpleNamespace(
        playbook_input={"flow_id": "missing-version", "flow_input": {}},
        action_input=None,
        json={
            "id": "timed-1",
            "name": "孤儿定时任务",
            "type": 0,
            "status": 0,
            "tactics": "2026-08-27 12:00:00",
        },
    )

    monkeypatch.setattr(
        timed_views,
        "Timed",
        SimpleNamespace(
            get_or_none=lambda **_filters: _AwaitableTimedQuery(timed)
        ),
    )

    async def fake_scope(_request):
        return False

    async def missing_input(_flow_id, tenant_id):
        raise CustomError(ErrorCode.PLAYBOOK_NOT_FOUND)

    monkeypatch.setattr(timed_views, "get_current_timed_internal_scope", fake_scope)
    monkeypatch.setattr(timed_views, "get_playbook_input", missing_input)
    monkeypatch.setattr(timed_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={"id": "timed-1"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )
    result = asyncio.run(inspect.unwrap(timed_views.get_timed_detail)(request))

    item = result["items"][0]
    assert item["name"] == "孤儿定时任务"
    assert item["tactics"] == "2026-08-27 12:00:00"
    assert item["playbook_missing"] is True
    assert item["input_args"] == []
