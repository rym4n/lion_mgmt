import inspect
from types import SimpleNamespace

import pytest

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
