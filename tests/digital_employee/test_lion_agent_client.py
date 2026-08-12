# coding=utf-8
import asyncio

import httpx
import pytest

from services.digital_employee import lion_agent_client as client_module


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://lion-agent/internal/v1/execute")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)


@pytest.mark.asyncio
async def test_execute_maps_non_retryable_agent_error(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Response(
                422,
                {
                    "detail": {
                        "code": "context_window_exceeded",
                        "message": "input too large",
                        "retryable": False,
                    }
                },
            )

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(client_module.LionAgentExecutionError) as exc_info:
        await client_module.LionAgentClient().execute(
            {"execution_id": "execution-1"},
        )

    assert exc_info.value.code == "context_window_exceeded"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_execute_timeout_requests_server_side_cancellation(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            request = httpx.Request("POST", "http://lion-agent/internal/v1/execute")
            raise httpx.ReadTimeout("timed out", request=request)

    cancelled = []

    async def cancel_execution(_self, execution_id):
        cancelled.append(execution_id)
        return True

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(client_module.LionAgentClient, "cancel_execution", cancel_execution)

    with pytest.raises(Exception) as exc_info:
        await client_module.LionAgentClient().execute(
            {"execution_id": "execution-timeout"},
        )

    assert exc_info.value.__class__.__name__ == "CustomError"
    assert cancelled == ["execution-timeout"]


@pytest.mark.asyncio
async def test_execute_task_cancellation_requests_server_side_cancellation(monkeypatch):
    started = asyncio.Event()

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    cancelled = []

    async def cancel_execution(_self, execution_id):
        cancelled.append(execution_id)
        return True

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(client_module.LionAgentClient, "cancel_execution", cancel_execution)

    task = asyncio.create_task(
        client_module.LionAgentClient().execute(
            {"execution_id": "execution-cancelled"},
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == ["execution-cancelled"]


@pytest.mark.asyncio
async def test_stream_close_requests_server_side_cancellation(monkeypatch):
    class _StreamResponse(_Response):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_lines(self):
            yield "event: delta"
            await asyncio.Event().wait()

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return _StreamResponse(200)

    cancelled = []

    async def cancel_execution(_self, execution_id):
        cancelled.append(execution_id)
        return True

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(client_module.LionAgentClient, "cancel_execution", cancel_execution)

    stream = client_module.LionAgentClient().stream_execute(
        {"execution_id": "execution-stream"},
    )
    assert await anext(stream) == "event: delta"
    await stream.aclose()

    assert cancelled == ["execution-stream"]
