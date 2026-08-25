# coding=utf-8
import asyncio
import inspect
from datetime import datetime
from types import SimpleNamespace

from services.system_config import views as system_views


def _run(handler, request):
    handler = inspect.unwrap(handler)
    return asyncio.run(handler(request))


def test_system_status_limits_query_to_recent_window_and_rebuilds_flattened_data(monkeypatch):
    calls = []

    async def fake_query_vlog(query, **kwargs):
        calls.append((query, kwargs))
        return [
            {
                "log_ip": "192.0.2.10",
                "ctime": "2026-08-25T03:00:00+0000",
                "data.lion-job.run_status": "running",
                "data.lion-job.health": "healthy",
                "data.lion-job.cpu_percent": "1.25%",
                "data.lion-job.mem_percent": "2.50%",
            }
        ]

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(
        system_views.get_system_status,
        SimpleNamespace(json={}),
    )

    assert len(calls) == 1
    query, kwargs = calls[0]
    assert 'log_type:"health_check"' in query
    assert "first 100 by (_time desc)" in query
    assert kwargs["limit"] == 100
    start = datetime.fromisoformat(kwargs["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(kwargs["end"].replace("Z", "+00:00"))
    assert 299 <= (end - start).total_seconds() <= 301
    assert result["data"] == [
        {
            "host": "192.0.2.10",
            "name": "lion-job",
            "run_status": "running",
            "health": "healthy",
            "cpu_percent": "1.25%",
            "mem_percent": "2.50%",
            "start_time": "",
            "check_time": "2026-08-25 11:00:00",
        }
    ]


def test_system_status_history_reads_flattened_data(monkeypatch):
    calls = []

    async def fake_query_vlog(query, **kwargs):
        calls.append((query, kwargs))
        return [
            {
                "log_ip": "192.0.2.10",
                "ctime": "2026-08-25T03:00:00+0000",
                "data.lion-job.run_status": "running",
                "data.lion-job.health": "healthy",
                "data.lion-job.cpu_percent": "1.25%",
                "data.lion-job.mem_usage": "10MiB / 1GiB",
                "data.lion-job.mem_percent": "2.50%",
            }
        ]

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})
    request = SimpleNamespace(
        json={
            "host": "192.0.2.10",
            "service_name": "lion-job",
            "start_time": "2026-08-25 10:00:00",
            "end_time": "2026-08-25 11:00:00",
        }
    )

    result = _run(system_views.get_system_status_history, request)

    assert len(calls) == 1
    assert "first 1500 by (_time desc)" in calls[0][0]
    assert result["data"][0]["name"] == "lion-job"
    assert result["data"][0]["cpu_percent"] == "1.25%"
    assert result["data"][0]["mem_usage"] == "10MiB / 1GiB"
