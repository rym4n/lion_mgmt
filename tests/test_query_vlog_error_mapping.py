# coding=utf-8
import asyncio

import httpx

from libs import utils


class _FakeHttpClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return self.response


class _CapturingHttpClient(_FakeHttpClient):
    def __init__(self, response):
        super().__init__(response)
        self.kwargs = None

    async def get(self, *_args, **kwargs):
        self.kwargs = kwargs
        return self.response


def _query_with_response(monkeypatch, response):
    monkeypatch.setattr(
        utils.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeHttpClient(response),
    )
    return asyncio.run(utils.query_vlog('_stream_id:"1"', project_id=13))


def test_invalid_stream_id_filter_is_treated_as_empty_result(monkeypatch):
    response = httpx.Response(
        400,
        request=httpx.Request("GET", "http://victoria-logs/select/logsql/query"),
        text=(
            'cannot parse query [_stream_id:1]: cannot parse _stream_id: '
            'cannot unmarshal _stream_id from "1"'
        ),
    )

    assert _query_with_response(monkeypatch, response) == []


def test_other_invalid_log_queries_remain_service_errors(monkeypatch):
    response = httpx.Response(
        400,
        request=httpx.Request("GET", "http://victoria-logs/select/logsql/query"),
        text="cannot parse query: unexpected token",
    )

    assert _query_with_response(monkeypatch, response) is None


def test_query_vlog_passes_offset_to_victoria_logs(monkeypatch):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "http://victoria-logs/select/logsql/query"),
        text='{"id":"audit-1"}\n',
    )
    client = _CapturingHttpClient(response)
    monkeypatch.setattr(utils.httpx, "AsyncClient", lambda **_kwargs: client)

    assert asyncio.run(utils.query_vlog("topic:lion_audit", project_id=0, limit=20, offset=40)) == [
        {"id": "audit-1"}
    ]
    assert client.kwargs["params"]["limit"] == 20
    assert client.kwargs["params"]["offset"] == 40
