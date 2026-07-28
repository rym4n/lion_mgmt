# coding=utf-8
import pytest

from services.agent import views as agent_views


@pytest.mark.asyncio
async def test_visible_agent_prefers_tenant_record(monkeypatch):
    tenant_agent = object()
    builtin_queries = []

    async def get_tenant_agent(tenant_id, agent_id=None, agent_key=None):
        assert tenant_id == "tenant-a"
        assert agent_id is None
        assert agent_key == "report_assistant"
        return tenant_agent

    class FakeCommandAgent:
        @staticmethod
        async def get_or_none(**kwargs):
            builtin_queries.append(kwargs)
            return object()

    monkeypatch.setattr(agent_views, "_get_agent", get_tenant_agent)
    monkeypatch.setattr(agent_views, "CommandAgent", FakeCommandAgent)

    result = await agent_views._get_visible_agent(
        "tenant-a",
        agent_key="report_assistant",
    )

    assert result is tenant_agent
    assert builtin_queries == []


@pytest.mark.asyncio
async def test_visible_agent_falls_back_to_global_builtin(monkeypatch):
    builtin_agent = object()
    builtin_queries = []

    async def get_tenant_agent(_tenant_id, agent_id=None, agent_key=None):
        assert agent_id is None
        assert agent_key == "report_assistant"
        return None

    class FakeCommandAgent:
        @staticmethod
        async def get_or_none(**kwargs):
            builtin_queries.append(kwargs)
            return builtin_agent

    monkeypatch.setattr(agent_views, "_get_agent", get_tenant_agent)
    monkeypatch.setattr(agent_views, "CommandAgent", FakeCommandAgent)

    result = await agent_views._get_visible_agent(
        "tenant-a",
        agent_key="report_assistant",
    )

    assert result is builtin_agent
    assert builtin_queries == [
        {
            "agent_key": "report_assistant",
            "is_builtin": True,
            "deleted": False,
        }
    ]
