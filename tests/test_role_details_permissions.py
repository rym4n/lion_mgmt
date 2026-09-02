# coding=utf-8
import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.error_code import ErrorCode  # noqa: E402
from libs.error_code.exception import CustomError  # noqa: E402
from services.role import views as role_views  # noqa: E402
from services.role.views import (  # noqa: E402
    _build_structured_permissions,
    _normalize_permission_menu,
    _ordered_permission_menus,
    _permission_menu_is_licensed,
    _playbook_permission_control,
    _playbook_permission_controls,
    _tool_resource_permission_control,
    _with_implicit_tool_view_permission,
)


tool_permission_migration = importlib.import_module(
    "migrations.models.90_20260724013000_refine_tool_market_permissions"
)
tool_resource_uncontrolled_migration = importlib.import_module(
    "migrations.models.91_20260726103000_default_tool_resource_uncontrolled"
)


PERMISSIONS_DATA = [
    {
        "model": "role",
        "description": "角色管理",
        "Permissions": [
            {"label": "新增角色权限", "value": "c"},
            {"label": "查看角色权限", "value": "r"},
        ],
    },
    {
        "model": "agent_report_schedule",
        "description": "汇报任务",
        "Permissions": [
            {"label": "新增汇报任务权限", "value": "c"},
            {"label": "删除汇报任务权限", "value": "d"},
            {"label": "修改汇报任务权限", "value": "u"},
            {"label": "查看汇报任务权限", "value": "r"},
        ],
    },
    {
        "model": "digital_employee",
        "description": "数字伙伴",
        "Permissions": [
            {"label": "新增数字伙伴权限", "value": "c"},
            {"label": "查看数字伙伴权限", "value": "r"},
        ],
    },
]

TOOL_PERMISSIONS_DATA = [
    {
        "model": "tool",
        "description": "应用管理",
        "bp": "tool_management_bp",
        "url": {
            "CN": {
                "meta": {"rank": 30, "title": "应用市场"},
                "path": "/toolManagement",
                "children": [
                    {
                        "meta": {"title": "应用市场"},
                        "path": "/toolManagement/index",
                    }
                ],
            }
        },
        "Permissions": [
            {"label": "查看页面", "value": "r"},
            {"label": "资源健康报告", "value": "tool:health:r"},
            {"label": "上传应用", "value": "tool:upload:c"},
            {"label": "删除应用", "value": "tool:app:d"},
            {"label": "新建资源", "value": "tool:resource:c"},
            {"label": "编辑资源", "value": "tool:resource:u"},
            {"label": "应用开发调试", "value": "tool:debug:r"},
            {"label": "删除资源", "value": "tool:resource:d"},
            {"label": "引擎修改", "value": "tool:engine:u"},
        ],
    }
]


def _by_model(structured):
    return {item["model"]: item for item in structured}


def test_role_details_expands_platform_wildcard_permissions():
    structured = _build_structured_permissions(["*:*"], PERMISSIONS_DATA)

    assert _by_model(structured)["role"]["sp"] == [
        {"c": "新增角色权限"},
        {"r": "查看角色权限"},
    ]
    assert _by_model(structured)["agent_report_schedule"]["sp"] == [
        {"c": "新增汇报任务权限"},
        {"d": "删除汇报任务权限"},
        {"u": "修改汇报任务权限"},
        {"r": "查看汇报任务权限"},
    ]


def test_role_details_expands_platform_wildcard_for_nested_asset_permissions():
    structured = _build_structured_permissions(
        ["*:*"],
        [
            {
                "model": "asset-management-models",
                "description": "资产模型",
                "Permissions": [
                    "asset_management:c",
                    "asset_management:r",
                ],
            }
        ],
    )

    assert structured == [
        {
            "model": "asset-management-models",
            "model_description": "资产模型",
            "sp": [
                {"asset_management:c": "新增资产管理权限"},
                {"asset_management:r": "查看资产管理权限"},
            ],
        }
    ]


def test_system_admin_role_update_has_a_specific_business_error():
    error = ErrorCode.ROLE_CANNOT_UPDATE.value

    assert error.code == 4186
    assert "系统管理员角色" in error.message


def test_update_role_rejects_the_system_admin_before_mutation(monkeypatch):
    role = SimpleNamespace(
        id="111111",
        name="系统管理员",
        description="超管账号,请勿轻易分配",
        permissions=["*:*"],
    )

    class _Role:
        @staticmethod
        async def get_or_none(**filters):
            assert filters == {
                "id": "111111",
                "deleted": False,
                "tenant_id": "tenant-a",
            }
            return role

    monkeypatch.setattr(role_views, "Role", _Role)
    request = SimpleNamespace(
        json={
            "role_id": "111111",
            "name": "系统管理员",
            "description": "changed",
            "permissions": ["dashboard:r"],
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    handler = role_views.update_role
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__

    try:
        asyncio.run(handler(request))
    except CustomError as error:
        assert error.enum is ErrorCode.ROLE_CANNOT_UPDATE
    else:
        raise AssertionError("system admin role update must be rejected")

    assert role.description == "超管账号,请勿轻易分配"
    assert role.permissions == ["*:*"]


def test_tenant_admin_role_has_a_specific_business_error():
    error = ErrorCode.TENANT_ADMIN_ROLE_PROTECTED.value

    assert error.code == 4191
    assert "租户管理员角色" in error.message


def test_add_role_rejects_reserved_tenant_admin_name():
    request = SimpleNamespace(
        json={
            "name": "租户管理员",
            "description": "duplicate",
            "permissions": ["dashboard:r"],
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(inspect.unwrap(role_views.add_role)(request))

    assert exc_info.value.enum is ErrorCode.TENANT_ADMIN_ROLE_PROTECTED


def test_update_role_rejects_tenant_admin_before_mutation(monkeypatch):
    role = SimpleNamespace(
        id="tenant-admin-role",
        name="租户管理员",
        description="租户管理员",
        permissions=["*:*"],
    )

    class _Role:
        @staticmethod
        async def get_or_none(**_filters):
            return role

    monkeypatch.setattr(role_views, "Role", _Role)
    request = SimpleNamespace(
        json={
            "role_id": role.id,
            "name": "renamed",
            "description": "changed",
            "permissions": ["dashboard:r"],
        },
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    handler = inspect.unwrap(role_views.update_role)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(handler(request))

    assert exc_info.value.enum is ErrorCode.TENANT_ADMIN_ROLE_PROTECTED
    assert role.name == "租户管理员"
    assert role.permissions == ["*:*"]


def test_update_role_rejects_renaming_custom_role_to_tenant_admin(monkeypatch):
    role = SimpleNamespace(
        id="custom-role",
        name="custom",
        description="custom",
        permissions=["dashboard:r"],
    )

    class _Role:
        @staticmethod
        async def get_or_none(**_filters):
            return role

    monkeypatch.setattr(role_views, "Role", _Role)
    request = SimpleNamespace(
        json={"role_id": role.id, "name": "租户管理员"},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(inspect.unwrap(role_views.update_role)(request))

    assert exc_info.value.enum is ErrorCode.TENANT_ADMIN_ROLE_PROTECTED
    assert role.name == "custom"


def test_delete_role_rejects_tenant_admin_even_when_unused(monkeypatch):
    role = SimpleNamespace(
        id="tenant-admin-role",
        name="租户管理员",
        deleted=False,
        saved=False,
    )

    async def save():
        role.saved = True

    role.save = save

    class _Role:
        @staticmethod
        async def get_or_none(**_filters):
            return role

    monkeypatch.setattr(role_views, "Role", _Role)
    request = SimpleNamespace(
        json={"role_id": role.id},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-a"}),
    )
    handler = inspect.unwrap(role_views.delete_role)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(handler(request))

    assert exc_info.value.enum is ErrorCode.TENANT_ADMIN_ROLE_PROTECTED
    assert role.deleted is False
    assert role.saved is False


def test_role_details_exposes_playbook_permission_control_mode():
    assert (
        _playbook_permission_control(
            ["dashboard:r", "workflow:r,u,uncontrolled"]
        )
        == "uncontrolled"
    )
    assert _playbook_permission_control(["workflow:r,u"]) == "controlled"
    assert _playbook_permission_control(["workflow:*"]) == "controlled"
    assert _playbook_permission_control(["*:*:"]) == "controlled"


def test_role_details_exposes_playbook_permission_control_by_action():
    assert _playbook_permission_controls(
        ["dashboard:r", "workflow:r", "workflow:u,uncontrolled"]
    ) == {
        "r": "controlled",
        "u": "uncontrolled",
        "d": "controlled",
    }


def test_platform_wildcard_has_uncontrolled_playbook_permission():
    assert _playbook_permission_control(["*:*", "dashboard:r"]) == "uncontrolled"
    assert _playbook_permission_controls(["*:*", "dashboard:r"]) == {
        "r": "uncontrolled",
        "u": "uncontrolled",
        "d": "uncontrolled",
    }


def test_role_details_exposes_tool_resource_permission_control():
    assert (
        _tool_resource_permission_control(
            ["dashboard:r", "tool:r", "tool:resource:uncontrolled"]
        )
        == "uncontrolled"
    )
    assert _tool_resource_permission_control(["tool:r"]) == "controlled"
    assert _tool_resource_permission_control(["tool:*"]) == "controlled"
    assert _tool_resource_permission_control(["*:*"]) == "uncontrolled"


def test_role_details_normalizes_legacy_nested_permission_codes():
    structured = _build_structured_permissions(
        [
            "agent:report:schedule:c,d,u,r",
            "digital_employee:role:r",
            "digital_employee:package:import",
        ],
        PERMISSIONS_DATA,
    )

    assert _by_model(structured)["agent_report_schedule"]["sp"] == [
        {"c": "新增汇报任务权限"},
        {"d": "删除汇报任务权限"},
        {"u": "修改汇报任务权限"},
        {"r": "查看汇报任务权限"},
    ]
    assert _by_model(structured)["digital_employee"]["sp"] == [
        {"c": "新增数字伙伴权限"},
        {"r": "查看数字伙伴权限"},
    ]


def test_role_details_tolerates_legacy_menu_string_permissions():
    permissions_data = [
        {
            "model": "agent_report_schedule",
            "description": "汇报任务",
            "Permissions": ["agent:report:schedule:c"],
        }
    ]

    structured = _build_structured_permissions(
        ["agent:report:schedule:c", "invalid", None, "unknown:r"],
        permissions_data,
    )

    assert structured == [
        {
            "model": "agent_report_schedule",
            "model_description": "汇报任务",
            "sp": [{"c": "新增汇报任务权限"}],
        }
    ]


def test_role_permission_all_normalizes_legacy_string_permissions():
    normalized = _normalize_permission_menu(
        {
            "model": "agent_report_schedule",
            "description": "汇报任务",
            "Permissions": ["agent:report:schedule:c", {"label": "查看汇报任务权限", "value": "agent:report:schedule:r"}],
        }
    )

    assert normalized["Permissions"] == [
        {"value": "c", "label": "新增汇报任务权限"},
        {"value": "r", "label": "查看汇报任务权限"},
    ]


def test_role_permission_all_exposes_actual_nested_permission_code():
    normalized = _normalize_permission_menu(
        {
            "model": "asset-management-models",
            "description": "资产模型",
            "Permissions": [
                {"label": "查看资产模型权限", "value": "asset_management:r"},
                {"label": "新增资产模型权限", "value": "asset_management:c"},
            ],
        }
    )

    assert normalized["Permissions"] == [
        {
            "value": "r",
            "label": "查看资产模型权限",
            "permission": "asset_management:r",
        },
        {
            "value": "c",
            "label": "新增资产模型权限",
            "permission": "asset_management:c",
        },
    ]


def test_role_permission_all_labels_split_menu_actions_without_collapsing_granularity():
    normalized = _normalize_permission_menu(
        {
            "model": "asset-management-models",
            "description": "资产模型",
            "Permissions": [
                "asset_management:c",
                "asset_management:r",
            ],
        }
    )

    assert normalized["Permissions"] == [
        {
            "value": "c",
            "label": "新增资产管理权限",
            "permission": "asset_management:c",
        },
        {
            "value": "r",
            "label": "查看资产管理权限",
            "permission": "asset_management:r",
        },
    ]


def test_role_permission_all_exposes_tool_market_fine_permissions():
    normalized = _normalize_permission_menu(TOOL_PERMISSIONS_DATA[0])

    assert normalized["description"] == "应用市场"
    assert normalized["Permissions"] == [
        {"value": "r", "label": "查看页面"},
        {"value": "r", "label": "资源健康报告", "permission": "tool:health:r"},
        {"value": "c", "label": "上传应用", "permission": "tool:upload:c"},
        {"value": "d", "label": "删除应用", "permission": "tool:app:d"},
        {"value": "c", "label": "新建资源", "permission": "tool:resource:c"},
        {"value": "u", "label": "编辑资源", "permission": "tool:resource:u"},
        {"value": "r", "label": "应用开发调试", "permission": "tool:debug:r"},
        {"value": "d", "label": "删除资源", "permission": "tool:resource:d"},
        {"value": "u", "label": "引擎修改", "permission": "tool:engine:u"},
    ]


def test_role_details_expands_legacy_tool_crud_to_all_refined_permissions():
    structured = _build_structured_permissions(
        ["tool:c,d,u,r"],
        TOOL_PERMISSIONS_DATA,
    )

    assert structured == [
        {
            "model": "tool",
            "model_description": "应用市场",
            "sp": [
                {"r": "查看页面"},
                {"tool:health:r": "资源健康报告"},
                {"tool:upload:c": "上传应用"},
                {"tool:app:d": "删除应用"},
                {"tool:resource:c": "新建资源"},
                {"tool:resource:u": "编辑资源"},
                {"tool:debug:r": "应用开发调试"},
                {"tool:resource:d": "删除资源"},
                {"tool:engine:u": "引擎修改"},
            ],
        }
    ]


def test_tool_fine_permissions_keep_page_permission_when_saved():
    assert _with_implicit_tool_view_permission(["tool:upload:c"]) == [
        "tool:upload:c",
        "tool:r",
    ]
    assert _with_implicit_tool_view_permission(["tool:r", "tool:upload:c"]) == [
        "tool:r",
        "tool:upload:c",
    ]


def test_tool_permission_migration_backfills_legacy_crud_roles():
    upgrade_sql = asyncio.run(tool_permission_migration.upgrade(None))

    assert "legacy.permission_value ~ '^tool:(\\*|[cdur](,[cdur])*)$'" in upgrade_sql
    assert "parsed.action_text = '*'" in upgrade_sql
    for permission in [
        "tool:r",
        "tool:health:r",
        "tool:upload:c",
        "tool:app:d",
        "tool:resource:c",
        "tool:resource:u",
        "tool:debug:r",
        "tool:resource:d",
        "tool:engine:u",
    ]:
        assert permission in upgrade_sql


def test_tool_permission_installer_seed_backfills_legacy_crud_roles():
    upgrade_sql = asyncio.run(tool_permission_migration.upgrade(None))
    init_sql = (ROOT.parent / "aioe_package/aioe_install/init.sql").read_text()

    for sql in (upgrade_sql, init_sql):
        assert "\"description\" = '应用市场'" in sql
        assert "legacy.permission_value ~ '^tool:(\\*|[cdur](,[cdur])*)$'" in sql
        assert "parsed.action_text = '*'" in sql
        for permission in [
            "tool:r",
            "tool:health:r",
            "tool:upload:c",
            "tool:app:d",
            "tool:resource:c",
            "tool:resource:u",
            "tool:debug:r",
            "tool:resource:d",
            "tool:engine:u",
        ]:
            assert permission in sql


def test_tool_resource_uncontrolled_migration_backfills_existing_tool_roles():
    upgrade_sql = asyncio.run(tool_resource_uncontrolled_migration.upgrade(None))

    assert "'tool:resource:uncontrolled'" in upgrade_sql
    assert "COALESCE(r.\"permissions\", '[]'::jsonb) ? 'tool:r'" in upgrade_sql
    assert (
        "NOT (COALESCE(r.\"permissions\", '[]'::jsonb) ? 'tool:resource:uncontrolled')"
        in upgrade_sql
    )


def test_tool_resource_uncontrolled_installer_seed_matches_migration_scope():
    init_sql = (ROOT.parent / "aioe_package/aioe_install/init.sql").read_text()

    assert "'tool:resource:uncontrolled'" in init_sql
    assert "COALESCE(r.\"permissions\"::jsonb, '[]'::jsonb) ? 'tool:r'" in init_sql
    assert (
        "NOT (COALESCE(r.\"permissions\"::jsonb, '[]'::jsonb) ? 'tool:resource:uncontrolled')"
        in init_sql
    )


def test_role_permission_all_sorts_and_labels_like_menu():
    permissions = [
        {
            "model": "workflow",
            "description": "可视化编排",
            "url": {
                "CN": {
                    "meta": {"rank": 40, "title": "剧本管理"},
                    "path": "/playbook",
                    "children": [{"meta": {"title": "剧本管理"}, "path": "/playbook/index"}],
                }
            },
            "Permissions": [],
        },
        {
            "model": "tool",
            "description": "应用管理",
            "url": {
                "CN": {
                    "meta": {"rank": 30, "title": "应用市场"},
                    "path": "/toolManagement",
                    "children": [
                        {"meta": {"title": "应用市场"}, "path": "/toolManagement/index"}
                    ],
                }
            },
            "Permissions": [],
        },
        {"model": "dashboard", "description": "仪表盘", "Permissions": []},
        {
            "model": "role",
            "description": "角色管理",
            "url": {
                "CN": {
                    "meta": {"rank": 99, "title": "系统设置"},
                    "path": "/System",
                    "children": [
                        {"meta": {"title": "角色管理"}, "path": "/System/Role/Role"}
                    ],
                }
            },
            "Permissions": [],
        },
        {
            "model": "user",
            "description": "账号管理",
            "url": {
                "CN": {
                    "meta": {"rank": 99, "title": "系统设置"},
                    "path": "/System",
                    "children": [
                        {"meta": {"title": "账号管理"}, "path": "/System/User/User"}
                    ],
                }
            },
            "Permissions": [],
        },
        {
            "model": "asset-management-catalog",
            "description": "资产数据",
            "url": {
                "CN": {
                    "meta": {"rank": 16, "title": "资产管理"},
                    "path": "/asset-management",
                    "children": [
                        {
                            "meta": {"title": "资产数据"},
                            "path": "/asset-management/catalog",
                        }
                    ],
                }
            },
            "Permissions": [],
        },
        {
            "model": "asset-management-models",
            "description": "资产模型",
            "url": {
                "CN": {
                    "meta": {"rank": 16, "title": "资产管理"},
                    "path": "/asset-management",
                    "children": [
                        {
                            "meta": {"title": "资产模型"},
                            "path": "/asset-management/models",
                        }
                    ],
                }
            },
            "Permissions": [],
        },
    ]

    ordered = [_normalize_permission_menu(item) for item in _ordered_permission_menus(permissions)]

    assert [item["model"] for item in ordered] == [
        "dashboard",
        "asset-management-models",
        "asset-management-catalog",
        "tool",
        "workflow",
        "user",
        "role",
    ]
    assert [item["description"] for item in ordered] == [
        "仪表盘",
        "资产模型",
        "资产数据",
        "应用市场",
        "剧本管理",
        "账号管理",
        "角色管理",
    ]


def test_role_permission_license_filter_accepts_old_tool_names():
    assert _permission_menu_is_licensed(
        TOOL_PERMISSIONS_DATA[0],
        {"自定义应用"},
    )


def test_role_details_groups_nested_permissions_under_split_menu_model():
    structured = _build_structured_permissions(
        [
            "asset_management:r",
            "network_topology:c",
            "network_topology:r",
            "network_topology:u",
            "network_topology:d",
            "network_topology:sync",
            "network_topology:reanalyze",
            "network_topology:profile_admin",
        ],
        [
            {
                "model": "asset-management-models",
                "description": "资产模型",
                "Permissions": [
                    {"label": "查看资产模型权限", "value": "asset_management:r"},
                    {"label": "新增资产模型权限", "value": "asset_management:c"},
                ],
            },
            {
                "model": "network-management-topology",
                "description": "网络拓扑",
                "Permissions": [
                    {"label": "新增网络拓扑权限", "value": "network_topology:c"},
                    {"label": "查看网络拓扑权限", "value": "network_topology:r"},
                    {"label": "修改网络拓扑权限", "value": "network_topology:u"},
                    {"label": "删除网络拓扑权限", "value": "network_topology:d"},
                    {"label": "同步拓扑", "value": "network_topology:sync"},
                    {
                        "label": "重新分析拓扑",
                        "value": "network_topology:reanalyze",
                    },
                    {
                        "label": "管理拓扑 Profile",
                        "value": "network_topology:profile_admin",
                    },
                ],
            },
        ],
    )

    assert _by_model(structured)["asset-management-models"]["sp"] == [
        {"asset_management:r": "查看资产模型权限"}
    ]
    assert _by_model(structured)["network-management-topology"]["sp"] == [
        {"network_topology:c": "新增网络拓扑权限"},
        {"network_topology:r": "查看网络拓扑权限"},
        {"network_topology:u": "修改网络拓扑权限"},
        {"network_topology:d": "删除网络拓扑权限"},
        {"network_topology:sync": "同步拓扑"},
        {"network_topology:reanalyze": "重新分析拓扑"},
        {"network_topology:profile_admin": "管理拓扑 Profile"},
    ]


def test_role_permission_all_exposes_skill_management_permissions():
    normalized = _normalize_permission_menu(
        {
            "model": "digital_employee_skill",
            "description": "技能管理",
            "Permissions": [
                {"label": "新增技能管理权限", "value": "c"},
                {"label": "删除技能管理权限", "value": "d"},
                {"label": "修改技能管理权限", "value": "u"},
                {"label": "查看技能管理权限", "value": "r"},
            ],
        }
    )

    assert normalized["Permissions"] == [
        {"value": "c", "label": "新增技能管理权限"},
        {"value": "d", "label": "删除技能管理权限"},
        {"value": "u", "label": "修改技能管理权限"},
        {"value": "r", "label": "查看技能管理权限"},
    ]


def test_role_details_groups_skill_permissions_under_skill_management_menu():
    structured = _build_structured_permissions(
        ["digital_employee_skill:r"],
        [
            {
                "model": "digital_employee_skill",
                "description": "技能管理",
                "Permissions": [
                    {"label": "查看技能管理权限", "value": "r"},
                    {"label": "修改技能管理权限", "value": "u"},
                ],
            },
        ],
    )

    assert structured == [
        {
            "model": "digital_employee_skill",
            "model_description": "技能管理",
            "sp": [{"r": "查看技能管理权限"}],
        }
    ]
