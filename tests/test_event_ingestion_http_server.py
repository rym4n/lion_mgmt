import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.event_ingestion import views as event_views


def test_http_server_source_can_explicitly_clear_retained_request_fields():
    current = {
        "type": "http_server",
        "address": "0.0.0.0:512",
        "encoding": "binary",
        "method": "GET",
        "path": "/",
        "strict_path": True,
        "response_code": 200,
        "headers": ["User-Agent"],
        "query_parameters": ["id", "type"],
        "host_key": "host",
        "path_key": "path",
    }

    source, _ = event_views._build_http_server_source(
        {
            "http_server_headers": [],
            "http_server_query_parameters": [],
            "http_server_host_key": "",
            "http_server_path_key": "",
        },
        current,
    )

    assert "headers" not in source
    assert "query_parameters" not in source
    assert "host_key" not in source
    assert "path_key" not in source


@pytest.mark.asyncio
async def test_http_server_get_without_body_uses_event_json_as_victorialogs_message():
    config = await event_views.gen_vector_config(
        {"source": {"type": "http_server"}}, "source", 1
    )

    transform = config["transforms"]["source_vl_transforms"]["source"]
    assert 'if message == ""' in transform
    assert "._msg = encode_json(.)" in transform
    assert "._msg = message" in transform


def test_json_parse_sample_is_compacted_to_one_stdin_event():
    sample = '{\n  "_msg": "post json",\n  "level": "info"\n}'

    assert event_views._prepare_parse_sample(sample, "json") == (
        '{"_msg":"post json","level":"info"}'
    )
    assert event_views._prepare_parse_sample(sample, "grok") == sample


def test_legacy_elasticsearch_sink_is_fully_normalized_for_victorialogs():
    config = {
        "sources": {"legacy": {"type": "kafka"}},
        "transforms": {
            "legacy_date_transforms": {
                "type": "remap",
                "inputs": ["legacy"],
                "source": '.datetime_str = "20260101000000"',
            },
            "legacy_transforms": {
                "type": "remap",
                "inputs": ["legacy_date_transforms"],
                "source": ".log = .",
            },
        },
        "sinks": {
            "legacy_sample_sinks": {
                "type": "elasticsearch",
                "inputs": ["legacy_transforms"],
                "endpoints": ["http://lion-es:9200"],
                "auth": {"strategy": "basic"},
                "bulk": {"index": "logs-legacy"},
            }
        },
    }

    event_views._normalize_victorialogs_config(config, "legacy", 31)

    transform = config["transforms"]["legacy_vl_transforms"]
    sink = config["sinks"]["legacy_sample_sinks"]
    assert transform["inputs"] == ["legacy_transforms"]
    assert "._msg" in transform["source"]
    assert sink == {
        "type": "http",
        "inputs": ["legacy_vl_transforms"],
        "uri": f"{event_views.VL_HOST}/insert/jsonline",
        "encoding": {"codec": "text"},
        "batch": {"max_bytes": 1048576},
        "request": {
            "headers": {
                "Content-Type": "application/json",
                "AccountID": "0",
                "ProjectID": "31",
            }
        },
        "compression": "gzip",
        "healthcheck": {"enabled": False},
    }


def test_binding_config_validates_once_after_all_rules_are_built(monkeypatch):
    validations = []
    monkeypatch.setattr(
        event_views,
        "check_vector_config",
        lambda config: validations.append(config) or True,
    )
    monkeypatch.setattr(event_views.time, "time", lambda: 1000)
    monkeypatch.setenv("RY_DB_USER", "postgres")
    monkeypatch.setenv("RY_DB_PASS", "secret")
    monkeypatch.setenv("RY_DB_HOST", "lion-db")
    monkeypatch.setenv("RY_DB_NAME", "lion_mgmt")
    event = SimpleNamespace(
        id="event-1",
        vector_name="source",
        event_ingestion_name="source event",
        transforms_type="json",
        vector_config_raw={
            "sources": {"source": {"type": "http_server"}},
            "transforms": {"source_transforms": {"type": "remap"}},
            "sinks": {},
        },
    )
    requests = [
        {
            "conditions": [
                {
                    "type": "ip_cidr_contains",
                    "name": "id",
                    "value": "192.0.0.0/16",
                }
            ],
            "event_name": '"cidr event"',
            "script_ids": ["script-a"],
            "linked_execution": 1,
            "concurrency": 2,
        },
        {
            "conditions": [{"type": "contains", "name": "level", "value": "high"}],
            "event_name": '"severity event"',
            "script_ids": [],
            "linked_execution": 0,
        },
    ]

    data, records = event_views._build_event_binding_config(event, "tenant-a", requests)

    assert len(validations) == 1
    assert validations[0] is data
    assert len(records) == 2
    first_topic = records[0]["topic"]
    second_topic = records[1]["topic"]
    assert first_topic != second_topic
    assert (
        data["transforms"][f"{first_topic}_transforms"]["condition"]
        == '(ip_cidr_contains("192.0.0.0/16", string!(.log."id")) ?? false)'
    )
    assert f"{first_topic}_script-a_sinks" in data["sinks"]
    assert f"{first_topic}_event_sinks" in data["sinks"]
    assert f"{second_topic}_event_sinks" in data["sinks"]


@pytest.mark.asyncio
async def test_binding_file_is_restored_when_database_transaction_fails(
    monkeypatch, tmp_path
):
    file_path = tmp_path / "event.yaml"
    file_path.write_text("old config\n", encoding="utf-8")
    event = SimpleNamespace(file_path=str(file_path))

    class _Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            raise RuntimeError("database commit failed")

    async def persist_bindings(*_args, **_kwargs):
        return None

    monkeypatch.setattr(event_views, "_persist_event_bindings", persist_bindings)

    with pytest.raises(RuntimeError, match="database commit failed"):
        await event_views._replace_event_bindings(
            event,
            "tenant-a",
            {"sources": {}, "transforms": {}, "sinks": {}},
            [],
            transaction_factory=lambda: _Transaction(),
        )

    assert file_path.read_text(encoding="utf-8") == "old config\n"
    assert not any(path.name.endswith(".tmp") for path in tmp_path.iterdir())


class _ConflictQuery:
    def __init__(self, exists):
        self._exists = exists
        self.excluded = None

    def exclude(self, **filters):
        self.excluded = filters
        return self

    async def exists(self):
        return self._exists


@pytest.mark.asyncio
async def test_syslog_conflict_check_is_tenant_scoped_and_can_exclude_current_row(
    monkeypatch,
):
    query = _ConflictQuery(True)
    captured = {}

    class _EventsIngestion:
        @staticmethod
        def filter(**filters):
            captured.update(filters)
            return query

    monkeypatch.setattr(event_views, "EventsIngestion", _EventsIngestion)

    conflict = await event_views._has_syslog_config_conflict(
        "udp514",
        "tenant-a",
        enabled_only=True,
        exclude_id="event-1",
    )

    assert conflict is True
    assert captured == {
        "syslog_config": "udp514",
        "deleted": False,
        "tenant_id": "tenant-a",
        "enabled_status": 1,
    }
    assert query.excluded == {"id": "event-1"}


@pytest.mark.asyncio
async def test_empty_syslog_config_never_queries_for_conflicts(monkeypatch):
    class _EventsIngestion:
        @staticmethod
        def filter(**_filters):
            pytest.fail("empty syslog config must not query EventsIngestion")

    monkeypatch.setattr(event_views, "EventsIngestion", _EventsIngestion)

    assert await event_views._has_syslog_config_conflict(None, "tenant-a") is False


def test_syslog_port_conflict_has_a_specific_business_error():
    error = ErrorCode.EVENT_INGESTION_SYSLOG_PORT_CONFLICT.value

    assert error.code == 4183
    assert "Syslog" in error.message
    assert "仅允许一条接入启用" in error.message


def test_listener_resources_normalize_tcp_syslog_and_http_server():
    syslog = {
        "sources": {
            "syslog": {
                "type": "socket",
                "mode": "tcp",
                "address": "0.0.0.0:525",
            }
        }
    }
    http_server = {
        "sources": {
            "http": {
                "type": "http_server",
                "address": "0.0.0.0:525",
            }
        }
    }

    assert event_views._listener_resources(syslog, "syslog") == {
        ("tcp", "0.0.0.0", 525)
    }
    assert event_views._listener_resources(http_server, "http") == {
        ("tcp", "0.0.0.0", 525)
    }


def test_event_source_summary_exposes_list_display_fields():
    kafka = SimpleNamespace(
        event_source_type="kafka",
        vector_name="source",
        vector_config_raw={
            "sources": {
                "source": {
                    "type": "kafka",
                    "topics": ["alerts", "audit"],
                }
            }
        },
        vector_config={},
        syslog_config=None,
    )
    syslog = SimpleNamespace(
        event_source_type="syslog",
        vector_name="source",
        vector_config_raw={
            "sources": {
                "source": {
                    "type": "socket",
                    "mode": "udp",
                    "address": "0.0.0.0:514",
                }
            }
        },
        vector_config={},
        syslog_config="udp514",
    )
    http_server = SimpleNamespace(
        event_source_type="http_server",
        vector_name="source",
        vector_config_raw={
            "sources": {
                "source": {
                    "type": "http_server",
                    "address": "0.0.0.0:8080",
                }
            }
        },
        vector_config={},
        syslog_config="http_server:0.0.0.0:8080",
    )

    assert event_views._event_source_summary(kafka) == {
        "topics": ["alerts", "audit"]
    }
    assert event_views._event_source_summary(syslog) == {
        "protocol": "udp",
        "port": 514,
    }
    assert event_views._event_source_summary(http_server) == {
        "address": "0.0.0.0:8080"
    }


@pytest.mark.asyncio
async def test_listener_conflict_is_global_and_detects_http_tcp_syslog_collision(
    monkeypatch,
):
    candidate = {
        "sources": {
            "candidate": {
                "type": "socket",
                "mode": "tcp",
                "address": "0.0.0.0:525",
            }
        }
    }
    captured = {}

    class _ListenerQuery:
        def exclude(self, **filters):
            captured["excluded"] = filters
            return self

        async def values(self, *fields):
            captured["fields"] = fields
            return [
                {
                    "event_source_type": "http_server",
                    "vector_name": "existing",
                    "vector_config": {
                        "sources": {
                            "existing": {
                                "type": "http_server",
                                "address": "0.0.0.0:525",
                            }
                        }
                    },
                    "vector_config_raw": {},
                    "syslog_config": "http_server:0.0.0.0:525",
                }
            ]

    class _EventsIngestion:
        @staticmethod
        def filter(**filters):
            captured["filters"] = filters
            return _ListenerQuery()

    monkeypatch.setattr(event_views, "EventsIngestion", _EventsIngestion)

    assert await event_views._has_listener_conflict(
        candidate,
        "candidate",
        exclude_id="event-1",
    )
    assert captured["filters"] == {"enabled_status": 1, "deleted": False}
    assert captured["excluded"] == {"id": "event-1"}
    assert "tenant_id" not in captured["filters"]


def test_udp_and_tcp_can_share_the_same_listener_port():
    assert not event_views._listener_resources_conflict(
        {("udp", "0.0.0.0", 525)},
        {("tcp", "0.0.0.0", 525)},
    )


def test_listener_conflict_has_a_specific_business_error():
    error = ErrorCode.EVENT_INGESTION_LISTENER_CONFLICT.value

    assert error.code == 4185
    assert "监听地址、协议和端口" in error.message


def test_syslog_conflict_keeps_the_existing_specific_error_contract():
    assert (
        event_views._listener_conflict_error("syslog", "syslog")
        is ErrorCode.EVENT_INGESTION_SYSLOG_PORT_CONFLICT
    )
    assert (
        event_views._listener_conflict_error("syslog", "http_server")
        is ErrorCode.EVENT_INGESTION_LISTENER_CONFLICT
    )


class _PipeInput:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    def flush(self):
        return None


class _PipeOutput:
    def __init__(self, output):
        self.output = output

    def readline(self):
        return self.output


class _Process:
    def __init__(self, output):
        self.stdin = _PipeInput()
        self.stdout = _PipeOutput(output)


class _Event:
    def __init__(self, status):
        self.project_id = 1
        self.convert_health_status = status
        self.saved_fields = []

    async def save(self, *, update_fields):
        self.saved_fields.append(update_fields)


async def _run_parse(
    monkeypatch,
    output,
    initial_status="healthy",
    messages=None,
    config_value="message_str = to_string!(.message)\n.log = parse_json!(message_str)",
    vector_valid=True,
):
    event = _Event(initial_status)
    process = _Process(output)

    class _Config:
        value = config_value

    class _Configs:
        @staticmethod
        async def get_or_none(*_args, **_kwargs):
            return _Config() if config_value is not None else None

    class _Events:
        @staticmethod
        async def get_or_none(**_kwargs):
            return event

    async def query_vlog(*_args, **_kwargs):
        if messages is not None:
            return messages
        return [{"_msg": '{\n  "_msg": "post json"\n}'}]

    monkeypatch.setattr(event_views, "Config", _Configs)
    monkeypatch.setattr(event_views, "EventsIngestion", _Events)
    monkeypatch.setattr(
        event_views, "check_vector_config", lambda *_args: vector_valid
    )
    monkeypatch.setattr(event_views, "query_vlog", query_vlog)
    monkeypatch.setattr(
        event_views.subprocess, "Popen", lambda *_args, **_kwargs: process
    )
    monkeypatch.setattr(event_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={"id": "event-1", "transforms_type": "json"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    try:
        result = await inspect.unwrap(event_views.parse_log)(request)
    except CustomError as error:
        result = error
    return result, event, process


@pytest.mark.asyncio
async def test_multiline_json_parse_is_sent_as_one_line_and_marks_healthy(monkeypatch):
    result, event, process = await _run_parse(
        monkeypatch,
        b'{"log":{"_msg":"post json"}}\n',
        initial_status=None,
    )

    assert process.stdin.data == b'{"_msg":"post json"}\n'
    assert result == {"_msg": "post json"}
    assert event.convert_health_status == "healthy"
    assert event.saved_fields == [["convert_health_status", "utime"]]


@pytest.mark.asyncio
async def test_failed_parse_clears_previous_healthy_status(monkeypatch):
    result, event, _process = await _run_parse(
        monkeypatch,
        b'{"message":"parse failed"}\n',
    )

    assert isinstance(result, str)
    assert event.convert_health_status is None
    assert event.saved_fields == [["convert_health_status", "utime"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected_error"),
    [
        ({"messages": []}, None),
        ({"config_value": None}, CustomError),
        ({"vector_valid": False}, CustomError),
    ],
)
async def test_unavailable_or_invalid_parse_input_clears_previous_healthy_status(
    monkeypatch, options, expected_error
):
    result, event, _process = await _run_parse(
        monkeypatch,
        b"",
        **options,
    )

    if expected_error:
        assert isinstance(result, expected_error)
    else:
        assert result == "当前不存在日志"
    assert event.convert_health_status is None
    assert event.saved_fields == [["convert_health_status", "utime"]]
