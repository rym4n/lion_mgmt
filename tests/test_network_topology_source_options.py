# coding=utf-8
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from services.network_topology import views


def _request(body, account=None):
    return SimpleNamespace(
        json=body,
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-1",
                "account_info": account,
            }
        ),
    )


class _ContextQuery:
    def __init__(self, model_name, initial_filters, calls):
        self.model_name = model_name
        self.initial_filters = dict(initial_filters)
        self.calls = calls

    def filter(self, **kwargs):
        self.calls.append((self.model_name, "filter", dict(kwargs)))
        return self

    def only(self, *fields):
        self.calls.append((self.model_name, "only", fields))
        return self

    async def all(self):
        self.calls.append((self.model_name, "all", None))
        return []


def _context_model(model_name, calls):
    class ContextModel:
        @classmethod
        def filter(cls, **kwargs):
            calls.append((model_name, "initial", dict(kwargs)))
            return _ContextQuery(model_name, kwargs, calls)

    return ContextModel


def test_source_option_context_scopes_every_model_to_tenant_and_limits_resources(
    monkeypatch,
):
    calls = []
    for attribute in (
        "Tool",
        "ToolAction",
        "ToolResources",
        "ToolResourcesModel",
        "ActionPermissions",
    ):
        monkeypatch.setattr(views, attribute, _context_model(attribute, calls))

    account = SimpleNamespace(id="account-1", roles=[])
    asyncio.run(
        views._load_source_option_context(
            "tenant-1",
            account,
            tool_name="network-device-app",
        )
    )

    initial_calls = {
        model_name: filters
        for model_name, operation, filters in calls
        if operation == "initial"
    }
    assert initial_calls == {
        "Tool": {
            "deleted": False,
            "tenant_id": "tenant-1",
            "maintain": False,
        },
        "ToolAction": {"deleted": False, "tenant_id": "tenant-1"},
        "ToolResources": {
            "deleted": False,
            "tenant_id": "tenant-1",
            "internal": False,
        },
        "ToolResourcesModel": {"deleted": False, "tenant_id": "tenant-1"},
        "ActionPermissions": {"deleted": False, "tenant_id": "tenant-1"},
    }
    assert (
        "ToolResources",
        "only",
        ("id", "tool_name", "name"),
    ) in calls
    assert ("Tool", "filter", {"name": "network-device-app"}) in calls
    for model_name in (
        "ToolAction",
        "ToolResources",
        "ToolResourcesModel",
        "ActionPermissions",
    ):
        assert (
            model_name,
            "filter",
            {"tool_name": "network-device-app"},
        ) in calls


def test_source_options_return_minimal_fields_and_filter_action_resource_permissions(
    monkeypatch,
):
    tenant_role = SimpleNamespace(
        id="role-tenant",
        tenant_id="tenant-1",
        permissions=[],
    )
    other_tenant_role = SimpleNamespace(
        id="role-other-tenant",
        tenant_id="tenant-2",
        permissions=["*:*"],
    )
    account = SimpleNamespace(
        id="account-1",
        roles=[tenant_role, other_tenant_role],
    )
    tool = SimpleNamespace(
        id="tool-version-1",
        name="network-device-app",
        version="1.0.0",
        description="网络设备采集应用",
    )
    allowed_action = SimpleNamespace(
        id="action-allowed",
        tool_name="network-device-app",
        version_number="1.0.0",
        action={
            "name": "获取运行配置",
            "func": "show_running_config",
            "classify": "query",
            "input": [
                {
                    "key": "scope",
                    "type": "string",
                    "required": True,
                    "description": "采集范围",
                    "default": "all",
                    "internal": "must-not-leak",
                },
                {
                    "key": "password",
                    "type": "password",
                    "required": True,
                    "description": "设备密码",
                    "default": "password-default-must-not-leak",
                    "value": "password-value-must-not-leak",
                },
                {
                    "key": "snmp_community",
                    "type": "text",
                    "required": False,
                    "description": "SNMP community",
                    "default": "community-default-must-not-leak",
                },
                {
                    "key": "token",
                    "type": "string",
                    "required": False,
                    "description": "API token",
                    "default": "token-default-must-not-leak",
                },
                {
                    "key": "api_key",
                    "type": "text",
                    "required": False,
                    "description": "API key",
                    "default": "api-key-default-must-not-leak",
                },
            ],
        },
    )
    denied_action = SimpleNamespace(
        id="action-denied",
        tool_name="network-device-app",
        version_number="1.0.0",
        action={
            "name": "关闭接口",
            "func": "shutdown_interface",
            "classify": "write",
            "input": [],
        },
    )
    allowed_resource = SimpleNamespace(
        id="resource-allowed",
        tool_name="network-device-app",
        name="核心交换机",
        resources="enc::encrypted-resource-secret-must-not-leak",
        status=1,
    )
    denied_resource = SimpleNamespace(
        id="resource-denied",
        tool_name="network-device-app",
        name="未授权防火墙",
        resources="plaintext-resource-secret-must-not-leak",
        status=1,
    )
    permission = SimpleNamespace(
        tool_name="network-device-app",
        action_name="show_running_config",
        users=[{}],
        roles=[{"resource-allowed": ["role-tenant"]}],
    )
    context_calls = []

    async def load_context(tenant_id, loaded_account, *, tool_name=None):
        assert tenant_id == "tenant-1"
        assert loaded_account is account
        context_calls.append(tool_name)
        return (
            {("network-device-app", "1.0.0"): tool},
            [allowed_action, denied_action],
            {
                "network-device-app": [
                    allowed_resource,
                    denied_resource,
                ]
            },
            {"network-device-app": True},
            {"network-device-app": [permission]},
        )

    selected_action_calls = []

    class FakeToolAction:
        @classmethod
        async def get_or_none(cls, **kwargs):
            selected_action_calls.append(kwargs)
            return allowed_action

    monkeypatch.setattr(views, "_load_source_option_context", load_context)
    monkeypatch.setattr(views, "ToolAction", FakeToolAction)
    monkeypatch.setattr(views, "response_ok", lambda data: data)

    tools = asyncio.run(
        inspect.unwrap(views.source_options)(_request({"kind": "tools"}, account))
    )
    versions = asyncio.run(
        inspect.unwrap(views.source_options)(
            _request(
                {"kind": "versions", "tool_name": "network-device-app"},
                account,
            )
        )
    )
    actions = asyncio.run(
        inspect.unwrap(views.source_options)(
            _request(
                {
                    "kind": "actions",
                    "tool_name": "network-device-app",
                    "tool_version": "1.0.0",
                },
                account,
            )
        )
    )
    resources = asyncio.run(
        inspect.unwrap(views.source_options)(
            _request(
                {"kind": "resources", "action_id": "action-allowed"},
                account,
            )
        )
    )

    assert tools == {
        "items": [
            {
                "name": "network-device-app",
                "label": "网络设备采集应用",
            }
        ]
    }
    assert versions == {
        "items": [
            {
                "id": "tool-version-1",
                "version": "1.0.0",
                "label": "1.0.0",
            }
        ]
    }
    assert actions == {
        "items": [
            {
                "id": "action-allowed",
                "name": "获取运行配置",
                "func": "show_running_config",
                "resource_required": True,
                "input": [
                    {
                        "key": "scope",
                        "type": "string",
                        "required": True,
                        "description": "采集范围",
                        "default": "all",
                    },
                    {
                        "key": "password",
                        "type": "password",
                        "required": True,
                        "description": "设备密码",
                    },
                    {
                        "key": "snmp_community",
                        "type": "password",
                        "required": False,
                        "description": "SNMP community",
                    },
                    {
                        "key": "token",
                        "type": "password",
                        "required": False,
                        "description": "API token",
                    },
                    {
                        "key": "api_key",
                        "type": "password",
                        "required": False,
                        "description": "API key",
                    },
                ],
            }
        ]
    }
    assert resources == {
        "items": [
            {
                "id": "resource-allowed",
                "name": "核心交换机",
            }
        ]
    }
    assert context_calls == [
        None,
        "network-device-app",
        "network-device-app",
        "network-device-app",
    ]
    assert selected_action_calls == [
        {
            "id": "action-allowed",
            "deleted": False,
            "tenant_id": "tenant-1",
        }
    ]

    serialized = repr(
        {
            "tools": tools,
            "versions": versions,
            "actions": actions,
            "resources": resources,
        }
    )
    for secret in (
        "must-not-leak",
        "password-default-must-not-leak",
        "password-value-must-not-leak",
        "community-default-must-not-leak",
        "token-default-must-not-leak",
        "api-key-default-must-not-leak",
        "encrypted-resource-secret-must-not-leak",
        "plaintext-resource-secret-must-not-leak",
        "resource-denied",
        "action-denied",
    ):
        assert secret not in serialized


def test_source_validation_rejects_resource_required_action_without_resource_selection(
    monkeypatch,
):
    from services.network_topology import refresh

    action = SimpleNamespace(
        id="action-1",
        tool_name="network-device-app",
        version_number="1.0.0",
        action={
            "name": "获取运行配置",
            "func": "show_running_config",
            "classify": "query",
            "input": [],
        },
    )
    tool = SimpleNamespace(
        name="network-device-app",
        version="1.0.0",
        main="main.py",
        description="网络设备采集应用",
    )
    lookups = []

    class FakeToolAction:
        @classmethod
        async def get_or_none(cls, **kwargs):
            lookups.append(("action", kwargs))
            return action

    class FakeTool:
        @classmethod
        async def get_or_none(cls, **kwargs):
            lookups.append(("tool", kwargs))
            return tool

    permission_checks = []

    async def requires_resource(tool_name, tenant_id):
        assert tool_name == "network-device-app"
        assert tenant_id == "tenant-1"
        return True

    async def check_permission(account, tool_name, action_func, resource, tenant_id):
        permission_checks.append(
            (account.id, tool_name, action_func, resource, tenant_id)
        )
        return False

    monkeypatch.setattr(refresh, "ToolAction", FakeToolAction)
    monkeypatch.setattr(refresh, "Tool", FakeTool)
    monkeypatch.setattr(refresh, "tool_requires_resource", requires_resource)
    monkeypatch.setattr(refresh, "check_action_execute_permission", check_permission)

    with pytest.raises(refresh.TopologyRefreshError) as exc_info:
        asyncio.run(
            refresh.validate_action_sources(
                [
                    {
                        "action_id": "action-1",
                        "resource_ids": [],
                        "action_input": {},
                    }
                ],
                "tenant-1",
                SimpleNamespace(id="account-1", roles=[]),
            )
        )

    assert str(exc_info.value) == "第 1 个动作来源必须选择至少一个资源"
    assert lookups == [
        (
            "action",
            {
                "id": "action-1",
                "tenant_id": "tenant-1",
                "deleted": False,
            },
        ),
        (
            "tool",
            {
                "name": "network-device-app",
                "version": "1.0.0",
                "tenant_id": "tenant-1",
                "deleted": False,
                "maintain": False,
            },
        ),
    ]
    assert permission_checks == []


def test_source_validation_rejects_write_actions_before_tool_execution(monkeypatch):
    from services.network_topology import refresh

    action = SimpleNamespace(
        id="action-write",
        tool_name="network-device-app",
        version_number="1.0.0",
        action={
            "name": "关闭接口",
            "func": "shutdown_interface",
            "classify": "write",
            "input": [],
        },
    )

    class FakeToolAction:
        @classmethod
        async def get_or_none(cls, **_kwargs):
            return action

    class UnexpectedTool:
        @classmethod
        async def get_or_none(cls, **_kwargs):
            raise AssertionError("write actions must be rejected before tool execution")

    monkeypatch.setattr(refresh, "ToolAction", FakeToolAction)
    monkeypatch.setattr(refresh, "Tool", UnexpectedTool)

    with pytest.raises(refresh.TopologyRefreshError) as exc_info:
        asyncio.run(
            refresh.validate_action_sources(
                [
                    {
                        "action_id": "action-write",
                        "resource_ids": [],
                        "action_input": {},
                    }
                ],
                "tenant-1",
                SimpleNamespace(id="account-1", roles=[]),
            )
        )

    assert "不是只读查询动作" in str(exc_info.value)
