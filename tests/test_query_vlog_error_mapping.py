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
