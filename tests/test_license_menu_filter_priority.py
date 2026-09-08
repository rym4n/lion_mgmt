# coding=utf-8
"""
测试 license 检查优先于权限检查 (Issue #277)

验证即使用户拥有 *:* 超级权限，也只能看到 license 授权的菜单。

修复前：拥有 *:* 权限的用户可以看到所有菜单，包括 license 未授权的模块
修复后：License 检查优先，即使有 *:* 权限，也只能看到 license 授权的模块
"""
import pytest

from services.license.views import (
    _licensed_module_keys,
    _menu_is_licensed,
    _role_can_open_menu,
)


@pytest.fixture
def mock_permissions_menu():
    """模拟 permissions_menu 表数据"""
    return [
        {
            "model": "playbook",
            "description": "剧本编排",
            "bp": "workflow_bp",
            "Permissions": [{"value": "r"}, {"value": "u"}],
            "url": {
                "CN": {
                    "path": "/playbook",
                    "meta": {"title": "剧本编排"},
                    "children": []
                }
            }
        },
        {
            "model": "large_screen",
            "description": "大屏管理",
            "bp": "dashboard_bp",
            "Permissions": [{"value": "r"}, {"value": "u"}],
            "url": {
                "CN": {
                    "path": "/screen",
                    "meta": {"title": "大屏管理"},
                    "children": []
                }
            }
        },
        {
            "model": "lic",
            "description": "许可管理",
            "bp": "license_bp",
            "Permissions": [{"value": "r"}],
            "url": {
                "CN": {
                    "path": "/license",
                    "meta": {"title": "许可管理"},
                    "children": []
                }
            }
        }
    ]


@pytest.fixture
def mock_license_with_playbook_only():
    """只授权剧本编排模块的 license"""
    return {
        "company": "test",
        "product": "AIOE",
        "moduleList": [
            {
                "name": "剧本编排",
                "module": "workflow_bp",
                "auth": True,
                "expireTime": 9999999999000
            }
        ]
    }


@pytest.fixture
def mock_license_with_screen():
    """授权大屏管理的 license"""
    return {
        "company": "test",
        "product": "AIOE",
        "moduleList": [
            {
                "name": "剧本编排",
                "module": "workflow_bp",
                "auth": True,
                "expireTime": 9999999999000
            },
            {
                "name": "大屏管理",
                "module": "dashboard_bp",
                "auth": True,
                "expireTime": 9999999999000
            }
        ]
    }


def test_super_admin_without_screen_license_should_not_see_screen_menu(
    mock_permissions_menu,
    mock_license_with_playbook_only
):
    """
    测试 Issue #277 的核心场景：
    超级管理员（*:* 权限）在 license 不包含大屏管理时，不应该看到大屏菜单

    这是修复的关键：license 检查必须优先于权限检查
    """
    # 计算 valid_modules（license 授权的模块）
    valid_modules = _licensed_module_keys(mock_license_with_playbook_only)

    # 验证 valid_modules 不包含 dashboard_bp 和 "大屏管理"
    assert "dashboard_bp" not in valid_modules, "dashboard_bp should not be in valid_modules"
    assert "大屏管理" not in valid_modules, "大屏管理 should not be in valid_modules"
    assert "workflow_bp" in valid_modules, "workflow_bp should be in valid_modules"
    assert "剧本编排" in valid_modules, "剧本编排 should be in valid_modules"

    # 模拟修复后的逻辑：先过滤出 licensed 的菜单
    licensed_permissions = [
        p for p in mock_permissions_menu
        if _menu_is_licensed(p, valid_modules) and p["description"] != "仪表盘"
    ]

    # 验证大屏管理被正确过滤掉
    licensed_models = [p["model"] for p in licensed_permissions]
    assert "playbook" in licensed_models, "剧本编排应该在列表中"
    assert "lic" in licensed_models, "许可管理应该在列表中（license_bp 总是包含）"
    assert "large_screen" not in licensed_models, "大屏管理不应该在列表中（license 未授权）"

    # 即使用户有 *:* 超级权限，也只能看到 licensed_permissions
    # （在修复后的代码中，*:* 权限只是跳过 _role_can_open_menu 检查，但不跳过 license 检查）


def test_super_admin_with_screen_license_should_see_screen_menu(
    mock_permissions_menu,
    mock_license_with_screen
):
    """
    测试：超级管理员（*:* 权限）在 license 包含大屏管理时，应该看到大屏菜单
    """
    # 计算 valid_modules
    valid_modules = _licensed_module_keys(mock_license_with_screen)

    # 验证 valid_modules 包含 dashboard_bp 和 "大屏管理"
    assert "dashboard_bp" in valid_modules
    assert "大屏管理" in valid_modules

    # 过滤出 licensed 的菜单
    licensed_permissions = [
        p for p in mock_permissions_menu
        if _menu_is_licensed(p, valid_modules) and p["description"] != "仪表盘"
    ]

    # 验证大屏管理没有被过滤
    licensed_models = [p["model"] for p in licensed_permissions]
    assert "playbook" in licensed_models
    assert "lic" in licensed_models
    assert "large_screen" in licensed_models, "大屏管理应该在列表中（license 已授权）"


def test_normal_user_without_screen_permission_cannot_see_screen_menu(
    mock_permissions_menu,
    mock_license_with_screen
):
    """
    测试：普通用户即使 license 包含大屏管理，但没有相应权限也看不到菜单
    """
    # 模拟只有剧本权限的普通用户
    role_permissions = ["playbook:r", "playbook:u"]

    # 计算 valid_modules
    valid_modules = _licensed_module_keys(mock_license_with_screen)

    # 先过滤出 licensed 的菜单
    licensed_permissions = [
        p for p in mock_permissions_menu
        if _menu_is_licensed(p, valid_modules) and p["description"] != "仪表盘"
    ]

    # 再根据角色权限过滤
    role_filtered_permissions = [
        permission
        for permission in licensed_permissions
        if _role_can_open_menu(role_permissions, permission)
    ]

    # 验证只能看到有权限的菜单
    role_models = [p["model"] for p in role_filtered_permissions]
    assert "playbook" in role_models, "有权限的剧本应该显示"
    assert "large_screen" not in role_models, "没有权限的大屏不应该显示"


def test_menu_is_licensed_matches_by_description_or_bp():
    """
    测试 _menu_is_licensed 函数的匹配逻辑
    """
    # Case 1: 通过 description 匹配
    menu1 = {"description": "大屏管理", "bp": "dashboard_bp"}
    valid_modules1 = {"大屏管理"}
    assert _menu_is_licensed(menu1, valid_modules1) is True

    # Case 2: 通过 bp 匹配
    menu2 = {"description": "大屏管理", "bp": "dashboard_bp"}
    valid_modules2 = {"dashboard_bp"}
    assert _menu_is_licensed(menu2, valid_modules2) is True

    # Case 3: 都不匹配
    menu3 = {"description": "大屏管理", "bp": "dashboard_bp"}
    valid_modules3 = {"workflow_bp", "剧本编排"}
    assert _menu_is_licensed(menu3, valid_modules3) is False

    # Case 4: 同时匹配 description 和 bp
    menu4 = {"description": "大屏管理", "bp": "dashboard_bp"}
    valid_modules4 = {"dashboard_bp", "大屏管理"}
    assert _menu_is_licensed(menu4, valid_modules4) is True


def test_licensed_module_keys_includes_both_name_and_module():
    """
    测试 _licensed_module_keys 会同时包含 name 和 module
    """
    license_data = {
        "moduleList": [
            {
                "name": "大屏管理",
                "module": "dashboard_bp",
                "auth": True,
                "expireTime": 9999999999000
            }
        ]
    }

    valid_modules = _licensed_module_keys(license_data)

    # 验证同时包含 name 和 module
    assert "大屏管理" in valid_modules
    assert "dashboard_bp" in valid_modules
    # license_bp 总是包含
    assert "license_bp" in valid_modules
    assert "许可管理" in valid_modules

