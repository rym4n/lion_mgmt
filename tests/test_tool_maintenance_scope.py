# coding=utf-8
import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security import authorization
from services.tools import views as tool_views


scope_migration = importlib.import_module(
    "migrations.models.94_20260730150000_scope_tool_resource_models"
)


def _request(maintenance_mode, payload=None):
    async def cache_get(key):
        assert key == "session-1"
        return maintenance_mode

    return SimpleNamespace(
        json=payload or {},
        ctx=SimpleNamespace(
            session={
                "id": "session-1",
                "tenant_id": "tenant-1",
            }
        ),
        app=SimpleNamespace(
            ctx=SimpleNamespace(cache_client=SimpleNamespace(get=cache_get))
        ),
    )


@pytest.mark.asyncio
async def test_same_application_only_lists_versions_from_current_mode(monkeypatch):
    rows = [
        {
            "id": "tool-a",
            "name": "same_app",
            "version": "version-a",
            "description": "维护模式 A 版本",
            "maintain": True,
            "internal": False,
            "deleted": False,
            "tenant_id": "tenant-1",
            "ctime": 2,
            "utime": 2,
            "path": "a.zip",
            "logo_path": "a.ico",
        },
        {
            "id": "tool-b",
            "name": "same_app",
            "version": "version-b",
            "description": "用户模式 B 版本",
            "maintain": False,
            "internal": False,
            "deleted": False,
            "tenant_id": "tenant-1",
            "ctime": 1,
            "utime": 1,
            "path": "b.zip",
            "logo_path": "b.ico",
        },
    ]
    filters = []

    class FakeQuery:
        def __init__(self, values, kwargs):
            self.rows = [row.copy() for row in values]
            self.kwargs = kwargs
            self.offset_value = 0
            self.limit_value = None

        def annotate(self, **_kwargs):
            return self

        def group_by(self, *_fields):
            return self

        def filter(self, **kwargs):
            self.kwargs.update(kwargs)
            return self

        def order_by(self, *_fields):
            return self

        def offset(self, value):
            self.offset_value = value
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def _filtered(self):
            result = self.rows
            for key, value in self.kwargs.items():
                if key == "name__in":
                    result = [row for row in result if row["name"] in value]
                elif key in {"name", "deleted", "maintain", "tenant_id"}:
                    result = [row for row in result if row[key] == value]
            end = None
            if self.limit_value is not None:
                end = self.offset_value + self.limit_value
            return result[self.offset_value:end]

        async def values(self, *fields):
            filtered = self._filtered()
            if fields == ("name", "latest_ctime"):
                latest = {}
                for row in filtered:
                    latest[row["name"]] = max(
                        latest.get(row["name"], row["ctime"]),
                        row["ctime"],
                    )
                return [
                    {"name": name, "latest_ctime": ctime}
                    for name, ctime in latest.items()
                ]
            if fields:
                return [
                    {field: row[field] for field in fields}
                    for row in filtered
                ]
            return filtered

        async def count(self):
            return len(self._filtered())

    class FakeTool:
        @staticmethod
        def filter(*_conditions, **kwargs):
            filters.append(kwargs.copy())
            return FakeQuery(rows, kwargs.copy())

    class FakeResourceModel:
        @staticmethod
        async def get_or_none(**_kwargs):
            return None

    async def keep_internal_flags(_tools, _tenant_id, _maintenance_mode):
        return None

    monkeypatch.setattr(tool_views, "Tool", FakeTool)
    monkeypatch.setattr(tool_views, "ToolResourcesModel", FakeResourceModel)
    monkeypatch.setattr(
        tool_views,
        "_apply_tool_group_internal_flags",
        keep_internal_flags,
    )
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    maintenance_list = await inspect.unwrap(tool_views.all_tools)(
        _request(True, {"page": 1, "size": 15})
    )
    user_list = await inspect.unwrap(tool_views.all_tools)(
        _request(False, {"page": 1, "size": 15})
    )
    maintenance_versions = await inspect.unwrap(tool_views.all_versions)(
        _request(True, {"name": "same_app"})
    )
    user_versions = await inspect.unwrap(tool_views.all_versions)(
        _request(False, {"name": "same_app"})
    )

    assert [tool["version"] for tool in maintenance_list["tools"]] == [
        "version-a"
    ]
    assert [tool["version"] for tool in user_list["tools"]] == ["version-b"]
    assert [tool["version"] for tool in maintenance_versions] == ["version-a"]
    assert [tool["version"] for tool in user_versions] == ["version-b"]
    assert any(item.get("maintain") is True for item in filters)
    assert any(item.get("maintain") is False for item in filters)


@pytest.mark.asyncio
async def test_cross_mode_version_cannot_read_actions(monkeypatch):
    class FakeTool:
        @staticmethod
        async def get_or_none(**kwargs):
            if kwargs["version"] == "version-a" and kwargs["maintain"] is True:
                return SimpleNamespace(id="tool-a")
            return None

    class FakeActionQuery:
        async def values(self, *_fields):
            return [
                {
                    "id": "action-a",
                    "action": {"func": "maintain_only"},
                    "playbook": [],
                }
            ]

    class FakeToolAction:
        @staticmethod
        def filter(**_kwargs):
            return FakeActionQuery()

    monkeypatch.setattr(tool_views, "Tool", FakeTool)
    monkeypatch.setattr(tool_views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(tool_views, "response_ok", lambda data: data)

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(tool_views.tool_actions)(
            _request(
                False,
                {"name": "same_app", "version": "version-a"},
            )
        )
    assert exc_info.value.enum is ErrorCode.TOOL_NOT_FOUND_ERROR

    result = await inspect.unwrap(tool_views.tool_actions)(
        _request(True, {"name": "same_app", "version": "version-a"})
    )
    assert [action["func"] for action in result["actions"]] == [
        "maintain_only"
    ]


def test_maintenance_upload_uses_distinct_version_namespace():
    user_version = tool_views.generate_tool_version(
        "same-file-hash",
        "tenant-1",
        False,
    )
    maintenance_version = tool_views.generate_tool_version(
        "same-file-hash",
        "tenant-1",
        True,
    )

    assert user_version == tool_views.generate_tool_version(
        "same-file-hash",
        "tenant-1",
    )
    assert maintenance_version != user_version


@pytest.mark.asyncio
async def test_action_execution_rejects_version_from_other_mode(monkeypatch):
    class FakeToolQuery:
        async def exists(self):
            return False

    class FakeTool:
        @staticmethod
        def filter(**kwargs):
            assert kwargs["version"] == "version-a"
            assert kwargs["maintain"] is False
            return FakeToolQuery()

    monkeypatch.setattr(authorization, "Tool", FakeTool)
    account = SimpleNamespace(id="admin-1", roles=[])

    allowed = await authorization.check_action_execute_permission(
        account,
        "same_app",
        "maintain_only",
        "",
        "tenant-1",
        False,
        "version-a",
    )

    assert allowed is False


def test_resource_model_migration_backfills_latest_application_scope():
    sql = asyncio.run(scope_migration.upgrade(None))
    downgrade_sql = asyncio.run(scope_migration.downgrade(None))

    assert 'ADD COLUMN IF NOT EXISTS "internal"' in sql
    assert 'SELECT tool."maintain"' in sql
    assert 'tool."name" = resource_model."tool_name"' in sql
    assert 'ORDER BY tool."ctime" DESC, tool."id" DESC' in sql
    assert 'INSERT INTO "tool_resources_model"' in sql
    assert "'m94-' || md5" in sql
    assert 'scope."internal"' in sql
    assert 'idx_tool_resource_model_mode_scope' in sql
    assert 'DELETE FROM "tool_resources_model" WHERE "id" LIKE \'m94-%\'' in downgrade_sql


def test_all_compatibility_surfaces_use_server_owned_mode_scope():
    from services.ai import views as ai_views
    from services.dashboard import views as dashboard_views
    from services.others import views as other_views
    from services.system_config import views as system_views
    from services.timed import views as timed_views
    from services.workflow import views as workflow_views
    from libs import chat

    assert "maintain=maintenance_mode" in inspect.getsource(
        inspect.unwrap(other_views.all_apps)
    )
    assert "internal=maintenance_mode" in inspect.getsource(
        inspect.unwrap(other_views.all_apps)
    )
    assert "maintain=maintenance_mode" in inspect.getsource(
        inspect.unwrap(ai_views.get_tool_info)
    )
    assert "internal=maintenance_mode" in inspect.getsource(
        inspect.unwrap(ai_views.get_tool_info)
    )
    assert "internal=maintenance_mode" in inspect.getsource(
        inspect.unwrap(dashboard_views.count_apps)
    )
    assert "maintain=maintenance_mode" in inspect.getsource(
        inspect.unwrap(workflow_views.import_workflow)
    )
    assert "maintenance_mode=maintenance_mode" in inspect.getsource(
        inspect.unwrap(system_views.init_data)
    )
    assert "cache_client.delete" not in inspect.getsource(
        inspect.unwrap(system_views.init_data)
    )
    assert "maintain=maintenance_mode" in inspect.getsource(chat.get_prompt_words)
    assert "internal=maintenance_mode" in inspect.getsource(chat.get_prompt_words)
    assert "return _current_timed_internal_scope(maintenance_mode)" in inspect.getsource(
        timed_views._timed_internal_scope_from_request
    )
