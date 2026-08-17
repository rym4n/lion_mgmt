# coding=utf-8
"""手动记忆维护入口（SPEC §5.8 lion_mgmt 侧引言，阶段一 jobs 限 update_index）。"""
import logging
import sys
from pathlib import Path

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
            import httpx

            request = httpx.Request("POST", "http://lion-agent/internal/v1/memory/maintain")
            raise httpx.HTTPStatusError(
                "failed", request=request, response=httpx.Response(self.status_code, request=request)
            )


@pytest.mark.asyncio
async def test_maintain_memory_sends_single_workspace_and_update_index_only(monkeypatch):
    """SPEC §5.8：一次一个 workspace、一把 key、jobs 限 update_index。"""
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
            return _Response(200, {"total": 1, "succeeded": 1, "failed": 0, "details": []})

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)

    result = await client_module.LionAgentClient().maintain_memory(
        tenant_id="tenant-1",
        employee_id="employee-1",
        llm={"base_url": "http://llm", "api_key": "sk-secret", "model": "qwen"},
        jobs=["update_index"],
    )

    assert captured["url"].endswith("/internal/v1/memory/maintain")
    assert captured["json"]["tenant_id"] == "tenant-1"
    assert captured["json"]["jobs"] == ["update_index"]
    assert captured["json"]["concurrency"] == 1
    assert len(captured["json"]["workspaces"]) == 1
    assert captured["json"]["workspaces"][0]["employee_id"] == "employee-1"
    assert result["succeeded"] == 1


@pytest.mark.asyncio
async def test_maintain_memory_failure_does_not_log_the_api_key(monkeypatch, caplog):
    """契约 §4.4：失败路径的日志与异常都不得出现 key 字面量。"""
    import httpx

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json=None, headers=None):
            request = httpx.Request("POST", url)
            raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", FakeAsyncClient)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception) as exc_info:
            await client_module.LionAgentClient().maintain_memory(
                tenant_id="tenant-1",
                employee_id="employee-1",
                llm={"base_url": "http://llm", "api_key": "sk-must-not-leak", "model": "qwen"},
                jobs=["update_index"],
            )

    assert "sk-must-not-leak" not in caplog.text
    assert "sk-must-not-leak" not in str(exc_info.value)


def test_maintain_jobs_constant_is_update_index_only():
    """阶段一白名单：auto_dream / optimize_index / reindex 都属阶段二。"""
    from services.digital_employee import views

    assert views.MEMORY_MAINTAIN_JOBS == ["update_index"]
