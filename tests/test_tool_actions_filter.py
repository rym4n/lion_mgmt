# coding=utf-8
import inspect
from types import SimpleNamespace

import pytest

from services.tools import views as tool_views


@pytest.mark.asyncio
async def test_tool_actions_filter_accepts_empty_payload_and_skips_invalid_rows(
    monkeypatch,
):
    async def current_scope(_request):
        return False

    async def scoped_pairs(_tenant_id, _maintenance_mode):
        return {
            ("valid-tool", "v1"),
            ("deleted-during-read", "v1"),
            ("malformed-tool", "v1"),
        }

    class FakeToolQuery:
        async def values(self, *fields):
            assert fields == ("name", "version", "description", "main")
            return [
                {
                    "name": "valid-tool",
                    "version": "v1",
                    "description": "有效工具",
                    "main": "main.py",
                }
            ]

    class FakeTool:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {
                "name__in": ["deleted-during-read", "malformed-tool", "valid-tool"],
                "deleted": False,
                "maintain": False,
                "tenant_id": "tenant-1",
            }
            return FakeToolQuery()

    class FakeActionQuery:
        async def values(self, *fields):
            assert fields == ("action", "id", "tool_name", "version_number")
            return [
                {
                    "action": {"func": "query", "name": "查询"},
                    "id": "action-1",
                    "tool_name": "valid-tool",
                    "version_number": "v1",
                },
                {
                    "action": None,
                    "id": "action-malformed",
                    "tool_name": "malformed-tool",
                    "version_number": "v1",
                },
                {
                    "action": {"func": "query_deleted", "name": "并发删除"},
                    "id": "action-deleted",
                    "tool_name": "deleted-during-read",
                    "version_number": "v1",
                },
            ]

    class FakeToolAction:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {"tenant_id": "tenant-1", "deleted": False}
            return FakeActionQuery()

    monkeypatch.setattr(tool_views, "_current_maintenance_mode", current_scope)
    monkeypatch.setattr(tool_views, "_scoped_tool_pairs", scoped_pairs)
    monkeypatch.setattr(tool_views, "Tool", FakeTool)
    monkeypatch.setattr(tool_views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}),
    )

    result = await inspect.unwrap(tool_views.tool_actions_filter)(request)

    assert result == {
        "actions": [
            {
                "func": "query",
                "name": "查询",
                "id": "action-1",
                "tool_name": "valid-tool",
                "version": "v1",
                "description": "有效工具",
                "tool_main": "main.py",
            }
        ]
    }
