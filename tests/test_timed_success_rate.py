# coding=utf-8

from types import SimpleNamespace

import pytest

from services.timed.views import (
    _parse_dashboard_datetime_range,
    _timed_internal_scope_from_request,
    _timed_name_filter_kwargs,
    dashboard_statistics,
    timer_success_rate,
)


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


def test_timed_name_filter_scopes_by_visible_internal_state():
    assert _timed_name_filter_kwargs("test", "tenant-1", False) == {
        "name": "test",
        "deleted": False,
        "tenant_id": "tenant-1",
        "internal": False,
    }


@pytest.mark.parametrize(
    ("scope", "maintenance_mode", "expected"),
    [
        (0, True, False),
        ("0", True, False),
        (1, False, True),
        ("1", False, True),
        (2, True, None),
        ("2", False, None),
        (None, True, True),
        (None, False, False),
    ],
)
def test_timed_internal_scope_prefers_request_scope(scope, maintenance_mode, expected):
    assert _timed_internal_scope_from_request(scope, maintenance_mode) is expected


def test_parse_dashboard_datetime_range_includes_end_day_midnight():
    start_dt, end_dt = _parse_dashboard_datetime_range("2026-04-01T00:00:00Z", "2026-04-30T00:00:00Z")

    assert start_dt.isoformat() == "2026-04-01T00:00:00+00:00"
    assert end_dt.isoformat() == "2026-04-30T23:59:59.999999+00:00"


@pytest.mark.asyncio
async def test_timer_success_rate_groups_node_logs_by_run_id(monkeypatch):
    timed = SimpleNamespace(id="timer-1", success_rate=None, result=None)
    saved = {}

    async def save():
        saved["called"] = True

    timed.save = save

    logs = [
        SimpleNamespace(run_id="run-1", status=0),
        SimpleNamespace(run_id="run-1", status=0),
        SimpleNamespace(run_id="run-2", status=0),
        SimpleNamespace(run_id="run-2", status=-1),
    ]
    recent_logs = [
        {"run_id": "run-2"},
        {"run_id": "run-1"},
    ]

    class FakeRunlog:
        @staticmethod
        def filter(**kwargs):
            assert kwargs.get("timer_id") == timed.id
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


@pytest.mark.asyncio
async def test_timer_success_rate_does_not_count_running_logs_as_success(monkeypatch):
    timed = SimpleNamespace(id="timer-1", success_rate=None, result=None)
    saved = {}

    async def save():
        saved["called"] = True

    timed.save = save

    logs = [
        SimpleNamespace(run_id="run-3", status=1),
        SimpleNamespace(run_id="run-3", status=2),
        SimpleNamespace(run_id="run-2", status=0),
        SimpleNamespace(run_id="run-1", status=0),
    ]
    recent_logs = [
        {"run_id": "run-3"},
        {"run_id": "run-2"},
        {"run_id": "run-1"},
    ]

    class FakeRunlog:
        @staticmethod
        def filter(**kwargs):
            assert kwargs.get("timer_id") == timed.id
            if "run_id__in" in kwargs:
                return _FakeQuery(logs)
            return _FakeQuery(recent_logs)

    monkeypatch.setattr("services.timed.views.Runlog", FakeRunlog)

    result = await timer_success_rate(timed)

    assert saved["called"] is True
    assert result == {
        "id": "timer-1",
        "result": "fail",
        "success_rate": "66.67%",
    }
    assert timed.result == "fail"
    assert timed.success_rate == "66.67%"


@pytest.mark.asyncio
async def test_dashboard_statistics_counts_status_one_as_failure(monkeypatch):
    captured = {}

    class FakeTimed:
        @staticmethod
        async def get_or_none(**kwargs):
            captured["timed_filter"] = kwargs
            return SimpleNamespace(id=kwargs["id"])

    class FakeConnection:
        async def execute_query_dict(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [
                {
                    "date": "2026-04-20",
                    "total_runs": 21,
                    "success_count": 15,
                    "fail_count": 6,
                }
            ]

    class FakeTransaction:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    def fake_response_ok(data):
        return data

    request = SimpleNamespace(
        json={
            "id": "timer-1",
            "start": "2026-04-01T00:00:00Z",
            "end": "2026-04-30T00:00:00Z",
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )

    monkeypatch.setattr("services.timed.views.Timed", FakeTimed)
    monkeypatch.setattr("services.timed.views.in_transaction", lambda: FakeTransaction())
    monkeypatch.setattr("services.timed.views.response_ok", fake_response_ok)

    result = await dashboard_statistics.__wrapped__.__wrapped__(request)

    assert "status NOT IN (0)" in captured["sql"]
    assert "status NOT IN (0, 1)" not in captured["sql"]
    assert captured["timed_filter"] == {
        "id": "timer-1",
        "deleted": False,
        "tenant_id": "tenant-1",
    }
    assert captured["params"][2].isoformat() == "2026-04-30T23:59:59.999999+00:00"
    assert result == [
        {
            "date": "2026-04-20",
            "total_runs": 21,
            "success_count": 15,
            "fail_count": 6,
            "success_rate": "71.0%",
        }
    ]
