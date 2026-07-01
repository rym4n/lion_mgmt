# coding=utf-8

from types import SimpleNamespace

import pytest

from services.timed.views import timer_success_rate


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def distinct(self):
        return self

    def annotate(self, **_kwargs):
        return self

    def group_by(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _limit):
        return self

    def values_list(self, *_args, **_kwargs):
        return self

    def values(self, *_args):
        return self

    async def all(self):
        return self._value

    def __await__(self):
        async def _result():
            return self._value

        return _result().__await__()


@pytest.mark.asyncio
async def test_timer_success_rate_groups_node_logs_by_run_id(monkeypatch):
    timed = SimpleNamespace(id="timer-1", success_rate=None, result=None)
    saved = {}

    async def save():
        saved["called"] = True

    timed.save = save

    logs = [
        SimpleNamespace(run_id="run-1", status=0),
        SimpleNamespace(run_id="run-1", status=1),
        SimpleNamespace(run_id="run-2", status=0),
        SimpleNamespace(run_id="run-2", status=2),
    ]
    recent_logs = [
        {"run_id": "run-2"},
        {"run_id": "run-1"},
    ]

    class FakeRunlog:
        @staticmethod
        def filter(**kwargs):
            if "run_id__in" in kwargs:
                return _FakeQuery(logs)
            return _FakeQuery(recent_logs)

    monkeypatch.setattr("services.timed.views.Runlog", FakeRunlog)

    result = await timer_success_rate(timed)

    assert saved["called"] is True
    assert result == {
        "id": "timer-1",
        "result": "fail",
        "success_rate": "50.0%",
    }
    assert timed.result == "fail"
    assert timed.success_rate == "50.0%"
