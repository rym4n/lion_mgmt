# coding=utf-8
import asyncio
import importlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
split_menus = importlib.import_module(
    "migrations.models.59_20260706150000_split_asset_network_management_menus"
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
