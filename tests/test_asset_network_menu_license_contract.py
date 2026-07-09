# coding=utf-8
import asyncio
import importlib
import re
from pathlib import Path

from services.license.views import _licensed_module_keys, _menu_is_licensed, _role_can_open_menu
from services.role.views import (
    _licensed_module_keys as _role_licensed_module_keys,
    _normalize_permission_menu,
    _permission_menu_is_licensed,
)


ROOT = Path(__file__).resolve().parents[2]
split_menus = importlib.import_module(
    "migrations.models.59_20260706150000_split_asset_network_management_menus"
)
fix_menus = importlib.import_module(
    "migrations.models.60_20260706165000_fix_asset_network_menu_permissions"
)
route_auth_meta = importlib.import_module(
    "migrations.models.63_20260708183000_add_asset_network_route_auth_meta"
)
command_role_permissions = importlib.import_module(
    "migrations.models.64_20260708223000_split_command_role_permissions"
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _assert_module_group(text: str, name: str, module: str, group: str):
    compact = _compact(text)
    assert (
        f'name:"{name}",module:"{module}",auth:true,group:"{group}"' in compact
        or f'"name":"{name}","module":"{module}","auth":True,"group":"{group}"'
        in compact
        or f'name="{name}",module="{module}",auth=true,group="{group}"' in compact
    )


def _assert_module_not_in_group(text: str, name: str, module: str, group: str):
    compact = _compact(text)
    forbidden_entries = [
        f'name:"{name}",module:"{module}",auth:true,group:"{group}"',
        f'"name":"{name}","module":"{module}","auth":True,"group":"{group}"',
        f'name="{name}",module="{module}",auth=true,group="{group}"',
    ]
    for entry in forbidden_entries:
        assert entry not in compact


def test_license_menu_permission_matching_supports_split_menu_models():
    asset_menu = {
        "model": "asset-management-models",
        "Permissions": [
            "supply_chain_review:model:c",
            "supply_chain_review:model:r",
        ],
    }
    legacy_menu = {
        "model": "system",
        "Permissions": [{"label": "查看系统设置权限", "value": "r"}],
    }

    assert _role_can_open_menu(["supply_chain_review:model:r"], asset_menu) is True
    assert _role_can_open_menu(["asset-management-models:r"], asset_menu) is False
    assert _role_can_open_menu(["system:c,d,u,r"], legacy_menu) is True
    assert _role_can_open_menu(
        ["command_center:r"],
        {
            "model": "command_center",
            "Permissions": ["command_center:c", "command_center:r"],
        },
    ) is True


def test_role_permission_menu_keeps_full_permission_values_for_split_modules():
    menu = {
        "model": "asset-management-models",
        "description": "资产模型",
        "Permissions": [
            "supply_chain_review:model:c",
            "supply_chain_review:model:r",
        ],
    }

    normalized = _normalize_permission_menu(menu)

    assert normalized["Permissions"] == [
        {
            "value": "c",
            "label": "新增资产模型权限",
            "permission": "supply_chain_review:model:c",
        },
        {
            "value": "r",
            "label": "查看资产模型权限",
            "permission": "supply_chain_review:model:r",
        },
    ]


def test_license_menu_matching_uses_active_license_module_bp_for_split_menus():
    license_data = {
        "grouplist": {
            "资产管理": {"expireTime": 4102444800000},
            "网络管理": {"expireTime": 4102444800000},
            "专家团队": {"expireTime": 4102444800000},
        },
        "moduleList": [
            {
                "name": "资产模型",
                "module": "asset_management_bp",
                "auth": True,
                "group": "资产管理",
            },
            {
                "name": "访问控制",
                "module": "network_access_control_bp",
                "auth": True,
                "group": "网络管理",
            },
            {
                "name": "指挥中心",
                "module": "command_bp",
                "auth": True,
                "group": "专家团队",
            },
        ],
    }
    valid_modules = _licensed_module_keys(license_data)

    assert _menu_is_licensed(
        {"description": "资产数据", "bp": "asset_management_bp"}, valid_modules
    )
    assert _menu_is_licensed(
        {"description": "网络拓扑", "bp": "topology_bp"}, valid_modules
    ) is False
    assert _menu_is_licensed(
        {"description": "访问控制", "bp": "network_access_control_bp"}, valid_modules
    )
    assert _menu_is_licensed(
        {"description": "指挥中心", "bp": "command_bp"}, valid_modules
    )


def test_role_permission_list_matching_uses_active_license_module_bp_for_split_menus():
    license_data = {
        "grouplist": {
            "资产管理": {"expireTime": 4102444800000},
            "网络管理": {"expireTime": 4102444800000},
        },
        "moduleList": [
            {
                "name": "资产模型",
                "module": "asset_management_bp",
                "auth": True,
                "group": "资产管理",
            },
            {
                "name": "访问控制",
                "module": "network_access_control_bp",
                "auth": True,
                "group": "网络管理",
            },
            {
                "name": "网络拓扑",
                "module": "topology_bp",
                "auth": False,
                "group": "网络管理",
            },
        ],
    }
    valid_modules = _role_licensed_module_keys(license_data)

    assert _permission_menu_is_licensed(
        {"description": "资产数据", "bp": "asset_management_bp"}, valid_modules
    )
    assert _permission_menu_is_licensed(
        {"description": "访问控制", "bp": "network_access_control_bp"}, valid_modules
    )
    assert _permission_menu_is_licensed(
        {"description": "网络拓扑", "bp": "topology_bp"}, valid_modules
    ) is False


def test_split_menu_migration_replaces_security_management_menu():
    upgrade_sql = asyncio.run(split_menus.upgrade(None))

    assert "description\" = '安全管理'" in upgrade_sql
    assert "asset-management-models" in upgrade_sql
    assert "asset-management-catalog" in upgrade_sql
    assert "network-management-topology" in upgrade_sql
    assert "network-management-access-control" in upgrade_sql
    assert "asset_management_bp" in upgrade_sql
    assert "topology_bp" in upgrade_sql
    assert "network_access_control_bp" in upgrade_sql
    menu_ids = re.findall(r"VALUES\s*\(\s*'([^']+)'", upgrade_sql)
    assert menu_ids
    assert all(len(menu_id) <= 36 for menu_id in menu_ids)
    assert '"icon":"ri:database-2-line"' in upgrade_sql
    assert '"icon":"ri:router-line"' in upgrade_sql
    assert '"icon":"ri:mind-map"' in upgrade_sql
    assert '"icon":"ri:shield-keyhole-line"' in upgrade_sql


def test_fix_menu_migration_removes_legacy_topology_and_backfills_admin_roles():
    upgrade_sql = asyncio.run(fix_menus.upgrade(None))

    assert "'network-topology'" in upgrade_sql
    assert "'/network-topology'" in upgrade_sql
    assert "'topology'" in upgrade_sql
    assert "'/topology'" in upgrade_sql
    assert "'/security-management'" in upgrade_sql
    assert "supply_chain_review:model:r" in upgrade_sql
    assert "supply_chain_review:ci:r" in upgrade_sql
    assert "network_topology:r" in upgrade_sql
    assert "supply_chain_review:policy:r" in upgrade_sql
    assert "'*:*'" in upgrade_sql
    assert "租户管理员" in upgrade_sql
    assert "空间管理员" in upgrade_sql
    assert "'role:c,d,u,r'" not in upgrade_sql


def test_asset_network_route_meta_carries_menu_read_permissions():
    upgrade_sql = asyncio.run(route_auth_meta.upgrade(None))
    init_sql = (ROOT / "aioe_package/aioe_install/init.sql").read_text()

    for text in (upgrade_sql, init_sql):
        assert "jsonb_set" in text
        assert "'permissions'" in text
        assert "'auths'" in text
        assert "supply_chain_review:model:r" in text
        assert "supply_chain_review:field:r" in text
        assert "supply_chain_review:relation:r" in text
        assert "supply_chain_review:ci:r" in text
        assert "network_topology:r" in text
        assert "supply_chain_review:policy:r" in text


def test_license_templates_use_asset_and_network_management_groups():
    license_files = [
        ROOT / "middleware/aioe_license/sign_lic.py",
        ROOT / "middleware/aioe_license/aioe_lic.js",
        ROOT / "middleware/aioe_license/www.hongcloud.net.conf",
    ]

    for path in license_files:
        text = path.read_text()
        assert "安全管理" not in text
        assert "资产管理" in text
        assert "网络管理" in text
        assert "asset_management_bp" in text
        assert "network_access_control_bp" in text
        _assert_module_group(text, "网络拓扑", "topology_bp", "网络管理")
        _assert_module_not_in_group(text, "网络拓扑", "topology_bp", "基础功能")


def test_command_center_migration_splits_expert_team_license_modules():
    command_menus = importlib.import_module(
        "migrations.models.62_20260708170000_fix_new_module_license_menu_contract"
    )
    upgrade_sql = asyncio.run(command_menus.upgrade(None))

    assert "command_bp" in upgrade_sql
    assert "digital_employee_bp" in upgrade_sql
    assert "agent_bp" in upgrade_sql
    assert "command_center_bp" not in upgrade_sql
    assert "'指挥中心'" in upgrade_sql
    assert "'数字员工'" in upgrade_sql
    assert "'汇报助手'" in upgrade_sql
    assert "command_center:r" in upgrade_sql
    assert "digital_employee:r" in upgrade_sql
    assert "agent:r" in upgrade_sql


def test_command_role_permission_backfill_splits_legacy_aggregate_permissions():
    upgrade_sql = asyncio.run(command_role_permissions.upgrade(None))
    init_sql = (ROOT / "aioe_package/aioe_install/init.sql").read_text()

    for text in (upgrade_sql, init_sql):
        assert "jsonb_array_elements_text" in text
        assert "command_center:c,d,u,r" in text
        assert "command_center:c,r,u,d" in text
        assert "command_center:r" in text
        assert "digital_employee:r" in text
        assert "agent:r" in text
        assert "agent_report_schedule:r" in text
        assert "agent:report:schedule:c,d,u,r" in text
