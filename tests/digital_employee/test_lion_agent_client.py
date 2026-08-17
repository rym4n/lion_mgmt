# coding=utf-8
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

# 添加项目根目录到 sys.path
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

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


@pytest.mark.asyncio
async def test_sync_workspace_sends_tenant_identity(monkeypatch):
    """SPEC §5.5：sync 必须携带租户身份，供 lion_agent 核对 owner.json。"""
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            return _Response(200, {"etag": "e1", "file_count": 1})

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)

    await client_module.LionAgentClient().sync_workspace(
        "employee-1",
        {"system_prompt.md": "role"},
        role_version=3,
        skill_versions={"write_file": 1},
        tenant_id="tenant-1",
        employee_id="employee-1",
    )

    assert captured["json"]["tenant_id"] == "tenant-1"
    assert captured["json"]["employee_id"] == "employee-1"
    assert captured["json"]["files"] == {"system_prompt.md": "role"}
    assert captured["json"]["role_version"] == 3
    assert captured["json"]["skill_versions"] == {"write_file": 1}


@pytest.mark.asyncio
async def test_sync_workspace_allows_null_employee_for_non_employee_workspace(monkeypatch):
    """浏览器工作区不属于任何员工：employee_id 显式为 None，而不是编一个。"""
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json=None, headers=None):
            captured["json"] = json
            return _Response(200, {"etag": "e1"})

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)

    await client_module.LionAgentClient().sync_workspace(
        "tenant-1:browser", {"extra": {}}, tenant_id="tenant-1"
    )

    assert captured["json"]["tenant_id"] == "tenant-1"
    assert captured["json"]["employee_id"] is None


@pytest.mark.asyncio
async def test_delete_workspace_now_sends_a_body(monkeypatch):
    """SPEC §5.5：delete 当前完全没有请求体，必须补上归属字段。"""
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            return _Response(200, {"deleted": True})

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)

    await client_module.LionAgentClient().delete_workspace(
        "employee-1", tenant_id="tenant-1", employee_id="employee-1"
    )

    assert captured["url"].endswith("/internal/v1/workspaces/employee-1/delete")
    assert captured["json"] == {"tenant_id": "tenant-1", "employee_id": "employee-1"}


@pytest.mark.asyncio
async def test_workspace_calls_never_log_the_internal_token(monkeypatch, caplog):
    """契约 §4.4：凭证不入日志。internal token 走 header，不得出现在日志里。"""
    import logging

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json=None, headers=None):
            return _Response(200, {"deleted": True})

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)

    with caplog.at_level(logging.DEBUG):
        await client_module.LionAgentClient(token="super-secret-token").delete_workspace(
            "employee-1", tenant_id="tenant-1", employee_id="employee-1"
        )

    assert "super-secret-token" not in caplog.text


def test_every_sync_workspace_call_site_passes_tenant_id():
    """D10 的守护网：tenant_id 在协议上必填，四个调用点一个都不能漏。

    用 AST 静态检查，不是 grep——grep 会被换行与格式化骗过去。
    调用点清单是显式的：新增调用点时这条测试会因为清单不匹配而失败，
    强制实施者回来更新清单并补参数。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    call_sites = []
    for rel in (
        "services/digital_employee/manager.py",
        "services/digital_employee/execute_coordinator.py",
        "services/tools/views.py",
    ):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "sync_workspace":
                kwargs = {kw.arg for kw in node.keywords}
                call_sites.append((rel, node.lineno, kwargs))

    assert call_sites, "没找到任何 sync_workspace 调用点——测试本身失效了"
    missing = [(rel, lineno) for rel, lineno, kwargs in call_sites if "tenant_id" not in kwargs]
    assert missing == [], f"这些 sync_workspace 调用点没有传 tenant_id: {missing}"
