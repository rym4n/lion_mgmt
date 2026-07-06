# coding=utf-8
import asyncio
import importlib
import re
from pathlib import Path

from services.license.views import _role_can_open_menu


ROOT = Path(__file__).resolve().parents[2]
split_menus = importlib.import_module(
    "migrations.models.59_20260706150000_split_asset_network_management_menus"
)
fix_menus = importlib.import_module(
    "migrations.models.60_20260706165000_fix_asset_network_menu_permissions"
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
