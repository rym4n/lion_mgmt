# coding=utf-8
import asyncio
import copy
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


def test_metrics_overview_groups_by_host_and_lists_middlewares(monkeypatch):
    calls = []

    async def fake_query_vlog(query, **kwargs):
        calls.append((query, kwargs))
        return [
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:30:00+0800",'
                    '"log_type":"middleware_metrics","data":{'
                    '"lion-redis":{"connected_clients":63},'
                    '"lion-db":{"active_conn":1},'
                    '"lion-mgmt":{"cpu_percent":1.2}}}'
                ),
            },
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:25:00+0800",'
                    '"log_type":"middleware_metrics","data":{"lion-redis":{"connected_clients":50}}}'
                ),
            },
            {
                "log_ip": "192.0.2.11",
                "_msg": (
                    '{"log_ip":"192.0.2.11","ctime":"2026-08-25T15:29:00+0800",'
                    '"log_type":"middleware_metrics","data":{"lion-worker":{"cpu_percent":0.5}}}'
                ),
            },
        ]

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(system_views.get_metrics_overview, SimpleNamespace(json={}))
    data = result["data"]

    query, kwargs = calls[0]
    assert 'log_type:"middleware_metrics"' in query
    assert "first 100 by (_time desc)" in query
    assert kwargs["limit"] == 100
    start = datetime.fromisoformat(kwargs["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(kwargs["end"].replace("Z", "+00:00"))
    assert 899 <= (end - start).total_seconds() <= 901

    assert len(data) == 2
    hosts = {item["host"]: item for item in data}
    assert set(hosts["192.0.2.10"]["middlewares"]) == {"lion-redis", "lion-db"}
    assert hosts["192.0.2.10"]["check_time"] == "2026-08-25 15:30:00"
    assert hosts["192.0.2.11"]["middlewares"] == []


def test_metrics_overview_returns_empty_when_no_data(monkeypatch):
    async def fake_query_vlog(query, **kwargs):
        return []

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(system_views.get_metrics_overview, SimpleNamespace(json={}))

    assert result["data"] == []


def test_is_cumulative_field_identifies_counter_fields():
    assert system_views._is_cumulative_field("net_input_bytes") is True
    assert system_views._is_cumulative_field("net_output_bytes") is True
    assert system_views._is_cumulative_field("block_read_bytes") is True
    assert system_views._is_cumulative_field("block_write_bytes") is True
    assert system_views._is_cumulative_field("rows_ingested_total") is True
    assert system_views._is_cumulative_field("total_commands_processed") is True
    assert system_views._is_cumulative_field("xact_commit") is True

    # 瞬时值不应被识别为累计值
    assert system_views._is_cumulative_field("connected_clients") is False
    assert system_views._is_cumulative_field("cache_hit_ratio") is False
    assert system_views._is_cumulative_field("mem_percent") is False
    assert system_views._is_cumulative_field("consumer_lag_total") is False


def test_apply_rate_diff_computes_deltas_and_nulls_first_point():
    points = [
        {"timestamp": "2026-08-25 15:00:00", "net_input_bytes": 1000, "connected_clients": 10},
        {"timestamp": "2026-08-25 15:05:00", "net_input_bytes": 3000, "connected_clients": 12},
        {"timestamp": "2026-08-25 15:10:00", "net_input_bytes": 8000, "connected_clients": 11},
    ]

    result = system_views._apply_rate_diff(points)

    # 首个点无前驱，速率为 None
    assert result[0]["net_input_bytes_rate"] is None
    # 后续点为与前一点的差值
    assert result[1]["net_input_bytes_rate"] == 2000
    assert result[2]["net_input_bytes_rate"] == 5000
    # 瞬时值字段不产生 _rate
    assert "connected_clients_rate" not in result[0]
    # 原始累计值保留
    assert result[2]["net_input_bytes"] == 8000


def test_apply_rate_diff_returns_none_on_counter_reset():
    # 容器重启导致计数器归零，差值为负
    points = [
        {"timestamp": "2026-08-25 15:00:00", "block_read_bytes": 9000},
        {"timestamp": "2026-08-25 15:05:00", "block_read_bytes": 100},
        {"timestamp": "2026-08-25 15:10:00", "block_read_bytes": 500},
    ]

    result = system_views._apply_rate_diff(points)

    assert result[0]["block_read_bytes_rate"] is None
    # 负差值返回 None，前端断线而非画成 0
    assert result[1]["block_read_bytes_rate"] is None
    # 重启后恢复正常差分
    assert result[2]["block_read_bytes_rate"] == 400


def test_apply_rate_diff_skips_missing_and_non_numeric_values():
    points = [
        {"timestamp": "t1", "net_input_bytes": 1000},
        {"timestamp": "t2"},                                  # 该点缺该字段
        {"timestamp": "t3", "net_input_bytes": 5000},
        {"timestamp": "t4", "net_input_bytes": "invalid"},     # 非数值
    ]

    result = system_views._apply_rate_diff(points)

    assert result[0]["net_input_bytes_rate"] is None
    assert result[1].get("net_input_bytes_rate") is None
    # t3 的前驱 t2 缺值，无法差分
    assert result[2]["net_input_bytes_rate"] is None
    assert result[3].get("net_input_bytes_rate") is None


def test_apply_rate_diff_handles_empty_and_single_point():
    assert system_views._apply_rate_diff([]) == []

    single = system_views._apply_rate_diff([{"timestamp": "t1", "net_input_bytes": 100}])
    assert single[0]["net_input_bytes_rate"] is None


def test_pick_bucket_hours_by_span():
    # 24 小时以内不降采样
    assert system_views._pick_bucket_hours(1) is None
    assert system_views._pick_bucket_hours(24) is None
    # 超过 24 小时按 1 小时桶
    assert system_views._pick_bucket_hours(25) == 1
    assert system_views._pick_bucket_hours(168) == 1
    # 超过 7 天按 4 小时桶
    assert system_views._pick_bucket_hours(169) == 4
    assert system_views._pick_bucket_hours(720) == 4


def test_downsample_averages_numeric_fields_within_bucket():
    points = [
        {"timestamp": "2026-08-25 10:00:00", "cpu_percent": 10.0, "connected_clients": 100},
        {"timestamp": "2026-08-25 10:20:00", "cpu_percent": 20.0, "connected_clients": 200},
        {"timestamp": "2026-08-25 10:40:00", "cpu_percent": 30.0, "connected_clients": 300},
        {"timestamp": "2026-08-25 11:10:00", "cpu_percent": 40.0, "connected_clients": 400},
    ]

    result = system_views._downsample(points, bucket_hours=1)

    assert len(result) == 2
    assert result[0]["timestamp"] == "2026-08-25 10:00:00"
    assert result[0]["cpu_percent"] == 20.0
    assert result[0]["connected_clients"] == 200.0
    assert result[1]["timestamp"] == "2026-08-25 11:00:00"
    assert result[1]["cpu_percent"] == 40.0


def test_downsample_preserves_string_fields_using_last_value():
    points = [
        {
            "timestamp": "2026-08-25 10:00:00",
            "rdb_last_bgsave_status": "ok",
            "cpu_percent": 10.0,
        },
        {
            "timestamp": "2026-08-25 10:30:00",
            "rdb_last_bgsave_status": "err",
            "cpu_percent": 20.0,
        },
    ]

    result = system_views._downsample(points, bucket_hours=1)

    assert len(result) == 1
    assert result[0]["rdb_last_bgsave_status"] == "err"
    assert result[0]["cpu_percent"] == 15.0


def test_downsample_ignores_none_values_in_average():
    points = [
        {"timestamp": "2026-08-25 10:00:00", "net_input_bytes_rate": None},
        {"timestamp": "2026-08-25 10:20:00", "net_input_bytes_rate": 100},
        {"timestamp": "2026-08-25 10:40:00", "net_input_bytes_rate": 200},
    ]

    result = system_views._downsample(points, bucket_hours=1)

    assert result[0]["net_input_bytes_rate"] == 150.0


def test_downsample_returns_all_none_bucket_as_none():
    points = [
        {"timestamp": "2026-08-25 10:00:00", "net_input_bytes_rate": None},
        {"timestamp": "2026-08-25 10:20:00", "net_input_bytes_rate": None},
    ]

    result = system_views._downsample(points, bucket_hours=1)

    assert result[0]["net_input_bytes_rate"] is None


def test_downsample_handles_empty_input():
    assert system_views._downsample([], bucket_hours=1) == []


def test_rate_diff_precedes_downsample_for_cumulative_metrics():
    points = [
        {"timestamp": "2026-08-25 10:00:00", "net_input_bytes": 0},
        {"timestamp": "2026-08-25 10:20:00", "net_input_bytes": 60},
        {"timestamp": "2026-08-25 10:40:00", "net_input_bytes": 120},
        {"timestamp": "2026-08-25 11:00:00", "net_input_bytes": 180},
    ]

    ordered = system_views._downsample(
        system_views._apply_rate_diff(copy.deepcopy(points)),
        bucket_hours=1,
    )
    reversed_order = system_views._apply_rate_diff(
        system_views._downsample(copy.deepcopy(points), bucket_hours=1)
    )

    assert ordered[0]["net_input_bytes_rate"] == 60.0
    assert ordered[1]["net_input_bytes_rate"] == 60.0
    assert reversed_order[0]["net_input_bytes_rate"] is None
    assert reversed_order[1]["net_input_bytes_rate"] == 120.0


def test_metrics_detail_rejects_missing_params(monkeypatch):
    import pytest

    from core.error_code import ErrorCode
    from libs.error_code.exception import CustomError

    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    with pytest.raises(CustomError) as exc_info:
        _run(
            system_views.get_metrics_detail,
            SimpleNamespace(json={"host": "192.0.2.10"}),
        )

    assert exc_info.value.enum == ErrorCode.PARAM_ERROR


def test_metrics_detail_rejects_invalid_time_format(monkeypatch):
    import pytest

    from core.error_code import ErrorCode
    from libs.error_code.exception import CustomError

    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    with pytest.raises(CustomError) as exc_info:
        _run(
            system_views.get_metrics_detail,
            SimpleNamespace(
                json={
                    "host": "192.0.2.10",
                    "middleware": "lion-redis",
                    "start_time": "not-a-timestamp",
                    "end_time": "2026-08-25T07:30:00Z",
                }
            ),
        )

    assert exc_info.value.enum == ErrorCode.PARAM_ERROR


def test_metrics_detail_extracts_series_sorted_ascending(monkeypatch):
    calls = []

    async def fake_query_vlog(query, **kwargs):
        calls.append((query, kwargs))
        return [
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:10:00+0800",'
                    '"log_type":"middleware_metrics","data":{"lion-redis":'
                    '{"connected_clients":70,"net_input_bytes":3000}}}'
                ),
            },
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:00:00+0800",'
                    '"log_type":"middleware_metrics","data":{"lion-redis":'
                    '{"connected_clients":63,"net_input_bytes":1000}}}'
                ),
            },
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:05:00+0800",'
                    '"log_type":"middleware_metrics","data":{"lion-redis":'
                    '{"connected_clients":65,"net_input_bytes":2000}}}'
                ),
            },
        ]

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(
        system_views.get_metrics_detail,
        SimpleNamespace(
            json={
                "host": "192.0.2.10",
                "middleware": "lion-redis",
                "start_time": "2026-08-25T07:00:00Z",
                "end_time": "2026-08-25T07:30:00Z",
            }
        ),
    )
    data = result["data"]

    query, kwargs = calls[0]
    assert 'log_type:"middleware_metrics"' in query
    assert 'log_ip:"192.0.2.10"' in query
    assert [p["connected_clients"] for p in data] == [63, 65, 70]
    assert data[0]["timestamp"] == "2026-08-25 15:00:00"
    assert data[0]["net_input_bytes_rate"] is None
    assert data[1]["net_input_bytes_rate"] == 1000
    assert data[2]["net_input_bytes_rate"] == 1000


def test_metrics_detail_filters_to_requested_middleware(monkeypatch):
    async def fake_query_vlog(query, **kwargs):
        return [
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:00:00+0800",'
                    '"log_type":"middleware_metrics","data":{'
                    '"lion-redis":{"connected_clients":63},'
                    '"lion-db":{"active_conn":5}}}'
                ),
            }
        ]

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(
        system_views.get_metrics_detail,
        SimpleNamespace(
            json={
                "host": "192.0.2.10",
                "middleware": "lion-db",
                "start_time": "2026-08-25T07:00:00Z",
                "end_time": "2026-08-25T07:30:00Z",
            }
        ),
    )

    assert result["data"][0]["active_conn"] == 5
    assert "connected_clients" not in result["data"][0]


def test_metrics_detail_falls_back_to_health_check_resource_metrics(monkeypatch):
    calls = []

    async def fake_query_vlog(query, **kwargs):
        calls.append((query, kwargs))
        if 'log_type:"middleware_metrics"' in query:
            return []
        return [
            {
                "log_ip": "192.0.2.10",
                "ctime": "2026-08-25T07:05:00+0000",
                "data.lion-db.cpu_percent": "1.25%",
                "data.lion-db.mem_percent": "26.50%",
            },
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:00:00+0800",'
                    '"log_type":"health_check","data":{"lion-db":'
                    '{"cpu_percent":"0.75%","mem_percent":"25.00%"}}}'
                ),
            },
        ]

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(
        system_views.get_metrics_detail,
        SimpleNamespace(
            json={
                "host": "192.0.2.10",
                "middleware": "lion-db",
                "start_time": "2026-08-25T07:00:00Z",
                "end_time": "2026-08-25T07:30:00Z",
            }
        ),
    )

    assert len(calls) == 2
    assert 'log_type:"health_check"' in calls[1][0]
    assert calls[1][1]["limit"] == 115
    assert result["data"] == [
        {
            "timestamp": "2026-08-25 15:00:00",
            "cpu_percent": 0.75,
            "mem_percent": 25.0,
        },
        {
            "timestamp": "2026-08-25 15:05:00",
            "cpu_percent": 1.25,
            "mem_percent": 26.5,
        },
    ]


def test_metrics_detail_returns_empty_for_unknown_middleware(monkeypatch):
    async def fake_query_vlog(query, **kwargs):
        return [
            {
                "log_ip": "192.0.2.10",
                "_msg": (
                    '{"log_ip":"192.0.2.10","ctime":"2026-08-25T15:00:00+0800",'
                    '"log_type":"middleware_metrics","data":{"lion-redis":'
                    '{"connected_clients":63}}}'
                ),
            }
        ]

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(
        system_views.get_metrics_detail,
        SimpleNamespace(
            json={
                "host": "192.0.2.10",
                "middleware": "lion-unknown",
                "start_time": "2026-08-25T07:00:00Z",
                "end_time": "2026-08-25T07:30:00Z",
            }
        ),
    )

    assert result["data"] == []


def test_metrics_detail_returns_empty_when_no_data(monkeypatch):
    async def fake_query_vlog(query, **kwargs):
        return []

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(
        system_views.get_metrics_detail,
        SimpleNamespace(
            json={
                "host": "192.0.2.10",
                "middleware": "lion-redis",
                "start_time": "2026-08-25T07:00:00Z",
                "end_time": "2026-08-25T07:30:00Z",
            }
        ),
    )

    assert result["data"] == []


def test_metrics_detail_downsamples_long_span(monkeypatch):
    points = []
    for hour in range(0, 72):
        for minute in (0, 30):
            points.append(
                {
                    "log_ip": "192.0.2.10",
                    "_msg": (
                        '{"log_ip":"192.0.2.10","ctime":"2026-08-%02dT%02d:%02d:00+0800",'
                        '"log_type":"middleware_metrics","data":{"lion-redis":'
                        '{"connected_clients":%d}}}'
                    )
                    % (20 + hour // 24, hour % 24, minute, 60 + hour),
                }
            )

    async def fake_query_vlog(query, **kwargs):
        return points

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    result = _run(
        system_views.get_metrics_detail,
        SimpleNamespace(
            json={
                "host": "192.0.2.10",
                "middleware": "lion-redis",
                "start_time": "2026-08-20T00:00:00Z",
                "end_time": "2026-08-23T00:00:00Z",
            }
        ),
    )
    data = result["data"]

    assert len(data) < 144
    assert all(p["timestamp"].endswith(":00:00") for p in data)


def test_metrics_detail_scales_query_limit_with_span(monkeypatch):
    captured = {}

    async def fake_query_vlog(query, **kwargs):
        captured.update(kwargs)
        captured["query"] = query
        return []

    monkeypatch.setattr(system_views, "query_vlog", fake_query_vlog)
    monkeypatch.setattr(system_views, "response_ok", lambda data: {"data": data})

    _run(
        system_views.get_metrics_detail,
        SimpleNamespace(
            json={
                "host": "192.0.2.10",
                "middleware": "lion-redis",
                "start_time": "2026-07-26T00:00:00Z",
                "end_time": "2026-08-25T00:00:00Z",
            }
        ),
    )

    assert captured["limit"] >= 8640
    assert captured["limit"] <= 20000
