# coding=utf-8
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.role.views import (
    _build_structured_permissions,
    _normalize_permission_menu,
    _preserve_superadmin_permission,
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


def test_role_details_groups_nested_permissions_under_split_menu_model():
    structured = _build_structured_permissions(
        ["supply_chain_review:model:r", "network_topology:r"],
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
                    {"label": "查看网络拓扑权限", "value": "network_topology:r"},
                    {"label": "编辑网络拓扑权限", "value": "network_topology:w"},
                ],
            },
        ],
    )

    assert _by_model(structured)["asset-management-models"]["sp"] == [
        {"supply_chain_review:model:r": "查看资产模型权限"}
    ]
    assert _by_model(structured)["network-management-topology"]["sp"] == [
        {"network_topology:r": "查看网络拓扑权限"}
    ]
