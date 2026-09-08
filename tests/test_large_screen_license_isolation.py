# coding=utf-8
"""大屏管理独立 license 授权 (aioe_bugs#277)

缺陷：大屏管理与仪表盘共用 module key `dashboard_bp`，导致
  1. 菜单侧：license 只要含「仪表盘」，`_menu_is_licensed` 就按 bp 放行大屏菜单
  2. 接口侧：大屏路由挂在无条件注册的 dashboard_bp 上，后端毫无 license 门禁

修复：大屏改用独立的 `large_screen_bp`，并对老 license 保留按名称识别的别名。
"""
import pytest

from services.app_factory import (
    LEGACY_LICENSE_MODULE_ALIASES,
    LICENSED_BLUEPRINT_SPECS,
    _resolve_licensed_module_key,
)
from services.license.views import _licensed_module_keys, _menu_is_licensed


FAR_FUTURE = 99999999999000


def _license(*modules):
    return {
        "company": "test",
        "moduleList": [
            {"name": name, "module": module, "auth": True, "expireTime": FAR_FUTURE}
            for name, module in modules
        ],
    }


# permissions_menu 中大屏的菜单行（迁移 124 之后的形态）
LARGE_SCREEN_MENU = {
    "model": "large_screen",
    "description": "大屏管理",
    "bp": "large_screen_bp",
}


# --- 菜单侧 ---------------------------------------------------------------


def test_dashboard_license_alone_does_not_unlock_the_large_screen_menu():
    """核心回归：只授权「仪表盘」时，大屏菜单必须隐藏。

    这正是 #277 的现场——测试环境 license 有仪表盘、无大屏管理，但菜单照常显示。
    """
    valid_modules = _licensed_module_keys(_license(("仪表盘", "dashboard_bp")))

    assert "dashboard_bp" in valid_modules  # 仪表盘确实被授权
    assert "大屏管理" not in valid_modules
    assert "large_screen_bp" not in valid_modules
    assert _menu_is_licensed(LARGE_SCREEN_MENU, valid_modules) is False


def test_large_screen_license_unlocks_the_large_screen_menu():
    valid_modules = _licensed_module_keys(
        _license(("仪表盘", "dashboard_bp"), ("大屏管理", "large_screen_bp"))
    )

    assert _menu_is_licensed(LARGE_SCREEN_MENU, valid_modules) is True


def test_legacy_license_naming_large_screen_still_unlocks_the_menu():
    """老 license 写的是 {name:大屏管理, module:dashboard_bp}，菜单仍按名称放行。"""
    valid_modules = _licensed_module_keys(_license(("大屏管理", "dashboard_bp")))

    assert _menu_is_licensed(LARGE_SCREEN_MENU, valid_modules) is True


# --- 蓝图注册侧 -----------------------------------------------------------


def test_large_screen_is_a_licensed_blueprint():
    """大屏路由必须走 license 门禁，不能再挂在无条件注册的 dashboard_bp 上。"""
    assert "large_screen_bp" in LICENSED_BLUEPRINT_SPECS
    assert LICENSED_BLUEPRINT_SPECS["large_screen_bp"] == (
        ("services.large_screen.views", "large_screen_bp"),
    )
    assert "dashboard_bp" not in LICENSED_BLUEPRINT_SPECS


def test_large_screen_routes_are_not_attached_to_dashboard_bp():
    from services.large_screen import views as large_screen_views

    assert large_screen_views.large_screen_bp.name == "large_screen"
    # 路径前缀保持 /dashboard，避免前端改动
    assert large_screen_views.large_screen_bp.url_prefix == "/dashboard"


@pytest.mark.parametrize(
    "item, expected",
    [
        ({"name": "大屏管理", "module": "large_screen_bp"}, "large_screen_bp"),
        # 老 license：module 是 dashboard_bp，靠名称别名兜底
        ({"name": "大屏管理", "module": "dashboard_bp"}, "large_screen_bp"),
        # 仪表盘不得解析出任何受控蓝图
        ({"name": "仪表盘", "module": "dashboard_bp"}, None),
        ({"name": None, "module": None}, None),
    ],
)
def test_resolve_licensed_module_key(item, expected):
    assert _resolve_licensed_module_key(item) == expected


def test_legacy_alias_only_covers_large_screen():
    assert LEGACY_LICENSE_MODULE_ALIASES == {"大屏管理": "large_screen_bp"}
