# coding=utf-8
import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.role.views import (  # noqa: E402
    _build_structured_permissions,
    _normalize_permission_menu,
    _playbook_permission_control,
    _preserve_superadmin_permission,
    _with_implicit_tool_view_permission,
)


tool_permission_migration = importlib.import_module(
    "migrations.models.90_20260724013000_refine_tool_market_permissions"
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
        "description": "数字员工",
        "Permissions": [
            {"label": "新增数字员工权限", "value": "c"},
            {"label": "查看数字员工权限", "value": "r"},
        ],
    },
]

TOOL_PERMISSIONS_DATA = [
    {
        "model": "tool",
        "description": "应用管理",
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
                    "supply_chain_review:model:c",
                    "supply_chain_review:model:r",
                    "supply_chain_review:field:r",
                ],
            }
        ],
    )

    assert structured == [
        {
            "model": "asset-management-models",
            "model_description": "资产模型",
            "sp": [
                {"supply_chain_review:model:c": "新增资产模型权限"},
                {"supply_chain_review:model:r": "查看资产模型权限"},
                {"supply_chain_review:field:r": "查看资产字段权限"},
            ],
        }
    ]


def test_role_update_cannot_remove_platform_wildcard_permission():
    superadmin_role = SimpleNamespace(permissions=["*:*"])

    assert _preserve_superadmin_permission(superadmin_role, ["screen:r"]) == [
        "screen:r",
        "*:*",
    ]
    assert _preserve_superadmin_permission(superadmin_role, ["*:*"]) == ["*:*"]
    assert _preserve_superadmin_permission(
        SimpleNamespace(permissions=["role:r"]), ["screen:r"]
    ) == ["screen:r"]


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


def test_platform_wildcard_has_uncontrolled_playbook_permission():
    assert _playbook_permission_control(["*:*", "dashboard:r"]) == "uncontrolled"


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
        {"c": "新增数字员工权限"},
        {"r": "查看数字员工权限"},
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
                {"label": "查看资产模型权限", "value": "supply_chain_review:model:r"},
                {"label": "新增资产模型权限", "value": "supply_chain_review:model:c"},
            ],
        }
    )

    assert normalized["Permissions"] == [
        {
            "value": "r",
            "label": "查看资产模型权限",
            "permission": "supply_chain_review:model:r",
        },
        {
            "value": "c",
            "label": "新增资产模型权限",
            "permission": "supply_chain_review:model:c",
        },
    ]


def test_role_permission_all_labels_split_menu_actions_without_collapsing_granularity():
    normalized = _normalize_permission_menu(
        {
            "model": "asset-management-models",
            "description": "资产模型",
            "Permissions": [
                "supply_chain_review:model:c",
                "supply_chain_review:field:c",
                "supply_chain_review:relation:c",
                "supply_chain_review:model:r",
                "supply_chain_review:field:r",
                "supply_chain_review:relation:r",
            ],
        }
    )

    assert normalized["Permissions"] == [
        {
            "value": "c",
            "label": "新增资产模型权限",
            "permission": "supply_chain_review:model:c",
        },
        {
            "value": "c",
            "label": "新增资产字段权限",
            "permission": "supply_chain_review:field:c",
        },
        {
            "value": "c",
            "label": "新增资产关系权限",
            "permission": "supply_chain_review:relation:c",
        },
        {
            "value": "r",
            "label": "查看资产模型权限",
            "permission": "supply_chain_review:model:r",
        },
        {
            "value": "r",
            "label": "查看资产字段权限",
            "permission": "supply_chain_review:field:r",
        },
        {
            "value": "r",
            "label": "查看资产关系权限",
            "permission": "supply_chain_review:relation:r",
        },
    ]


def test_role_permission_all_exposes_tool_market_fine_permissions():
    normalized = _normalize_permission_menu(TOOL_PERMISSIONS_DATA[0])

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
            "model_description": "应用管理",
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
        assert "\"description\" = '应用管理'" in sql
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


def test_role_details_groups_nested_permissions_under_split_menu_model():
    structured = _build_structured_permissions(
        [
            "supply_chain_review:model:r",
            "network_topology:c",
            "network_topology:r",
            "network_topology:u",
            "network_topology:d",
        ],
        [
            {
                "model": "asset-management-models",
                "description": "资产模型",
                "Permissions": [
                    {"label": "查看资产模型权限", "value": "supply_chain_review:model:r"},
                    {"label": "新增资产模型权限", "value": "supply_chain_review:model:c"},
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
                ],
            },
        ],
    )

    assert _by_model(structured)["asset-management-models"]["sp"] == [
        {"supply_chain_review:model:r": "查看资产模型权限"}
    ]
    assert _by_model(structured)["network-management-topology"]["sp"] == [
        {"network_topology:c": "新增网络拓扑权限"},
        {"network_topology:r": "查看网络拓扑权限"},
        {"network_topology:u": "修改网络拓扑权限"},
        {"network_topology:d": "删除网络拓扑权限"},
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
