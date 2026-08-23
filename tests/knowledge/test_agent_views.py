# coding=utf-8
"""Agent 面隔离行为（SPEC §10.2、§15.2）。"""

import inspect
from pathlib import Path

import pytest

from libs.error_code.exception import CustomError


class _FakeEmployee:
    def __init__(self, **kw):
        self.id = kw.get("id", "emp-1")
        self.tenant_id = kw.get("tenant_id", "t1")
        self.knowledge_space_keys = kw.get("space_keys", ["ops"])
        self.knowledge_write_space = kw.get("write_space")
        self.deleted = False


@pytest.mark.asyncio
async def test_search_limits_target_uri_to_visible_spaces(monkeypatch):
    from services.knowledge import agent_views

    captured = {}

    async def fake_find(query, *, target_uri, limit=5, level=None):
        captured["target_uri"] = target_uri
        return {"contexts": []}

    class _Client:
        find = staticmethod(fake_find)

    async def fake_get_client(tenant_id):
        return _Client()

    async def fake_roots(tenant_id, space_keys):
        return [f"viking://resources/spaces/{key}" for key in space_keys]

    monkeypatch.setattr(agent_views, "get_knowledge_client", fake_get_client)
    monkeypatch.setattr(agent_views.scope, "visible_space_roots", fake_roots)

    employee = _FakeEmployee(space_keys=["ops", "runbook"])
    await agent_views.do_search(employee, "磁盘满了", space_key=None, top_k=5)

    assert captured["target_uri"] == [
        "viking://resources/spaces/ops",
        "viking://resources/spaces/runbook",
    ]


@pytest.mark.asyncio
async def test_search_with_unbound_space_returns_empty(monkeypatch):
    from services.knowledge import agent_views

    called = {"find": False}

    class _Client:
        @staticmethod
        async def find(query, *, target_uri, limit=5, level=None):
            called["find"] = True
            return {"contexts": []}

    async def fake_get_client(tenant_id):
        return _Client()

    async def fake_roots(tenant_id, space_keys):
        return [f"viking://resources/spaces/{key}" for key in space_keys]

    monkeypatch.setattr(agent_views, "get_knowledge_client", fake_get_client)
    monkeypatch.setattr(agent_views.scope, "visible_space_roots", fake_roots)

    employee = _FakeEmployee(space_keys=["ops"])
    result = await agent_views.do_search(employee, "x", space_key="secret", top_k=5)

    assert result == []
    assert called["find"] is False


@pytest.mark.asyncio
async def test_search_with_no_binding_returns_empty(monkeypatch):
    from services.knowledge import agent_views

    async def fake_roots(tenant_id, space_keys):
        return []

    monkeypatch.setattr(agent_views.scope, "visible_space_roots", fake_roots)

    employee = _FakeEmployee(space_keys=[])
    assert await agent_views.do_search(employee, "x", space_key=None, top_k=5) == []


@pytest.mark.asyncio
async def test_read_rejects_document_outside_visible_spaces(monkeypatch):
    from core.error_code import ErrorCode
    from services.knowledge import agent_views

    class _Doc:
        id = "doc-9"
        space_key = "secret"
        uri = "viking://resources/spaces/secret/a.md"
        title = "机密"
        deleted = False

    async def fake_get_doc(tenant_id, document_id):
        return _Doc()

    monkeypatch.setattr(agent_views, "get_document_or_404", fake_get_doc)

    employee = _FakeEmployee(space_keys=["ops"])
    with pytest.raises(CustomError) as excinfo:
        await agent_views.do_read(employee, "doc-9")

    assert excinfo.value.enum is ErrorCode.KNOWLEDGE_DOCUMENT_NOT_FOUND


@pytest.mark.asyncio
async def test_write_without_write_space_is_rejected():
    from core.error_code import ErrorCode
    from services.knowledge import agent_views

    employee = _FakeEmployee(space_keys=["ops"], write_space=None)
    with pytest.raises(CustomError) as excinfo:
        await agent_views.do_write(employee, title="t", content="c", path=None)

    assert excinfo.value.enum is ErrorCode.KNOWLEDGE_WRITE_NOT_ALLOWED


@pytest.mark.asyncio
async def test_write_goes_to_configured_space(monkeypatch):
    from services.knowledge import agent_views

    captured = {}

    async def fake_upsert(tenant_id, **kwargs):
        captured.update(kwargs)
        captured["tenant_id"] = tenant_id
        return {"id": "doc-new", "status": "processing"}

    monkeypatch.setattr(agent_views, "upsert_document", fake_upsert)

    employee = _FakeEmployee(space_keys=["ops", "runbook"], write_space="runbook")
    await agent_views.do_write(employee, title="处置记录", content="内容", path=None)

    assert captured["space_key"] == "runbook"
    assert captured["source"] == "agent"
    assert captured["source_ref"] == "emp-1"
    assert captured["rel_path"].endswith(".md")


def test_write_cannot_target_readonly_space():
    from services.knowledge import agent_views

    sig = inspect.signature(agent_views.do_write)
    assert "space" not in sig.parameters
    assert "space_key" not in sig.parameters


@pytest.mark.asyncio
async def test_delete_rejects_document_in_readonly_space(monkeypatch):
    from services.knowledge import agent_views

    class _Doc:
        id = "doc-1"
        space_key = "ops"
        uri = "viking://resources/spaces/ops/a.md"
        deleted = False

    async def fake_get_doc(tenant_id, document_id):
        return _Doc()

    monkeypatch.setattr(agent_views, "get_document_or_404", fake_get_doc)

    employee = _FakeEmployee(space_keys=["ops", "runbook"], write_space="runbook")
    with pytest.raises(CustomError):
        await agent_views.do_delete(employee, "doc-1")


def test_agent_blueprint_prefix():
    src = (Path(__file__).resolve().parents[2] / "services" / "knowledge" / "agent_views.py").read_text(encoding="utf-8")
    assert 'url_prefix="/knowledge/agent"' in src


def test_agent_views_never_reads_tenant_from_body():
    src = (Path(__file__).resolve().parents[2] / "services" / "knowledge" / "agent_views.py").read_text(encoding="utf-8")
    assert 'data.get("tenant_id")' not in src
    assert 'params.get("tenant_id")' not in src
