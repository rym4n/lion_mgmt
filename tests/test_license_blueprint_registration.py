# coding=utf-8
import time

from libs.security.license_modules import is_licensed_module_active


def test_licensed_module_active_uses_group_expiry_for_legacy_license_payload():
    license_data = {
        "grouplist": {
            "全平台AI助手": {
                "expireTime": int(time.time() * 1000) + 60000,
            }
        }
    }
    item = {
        "name": "数字员工",
        "module": "digital_employee_bp",
        "auth": True,
        "group": "全平台AI助手",
    }

    assert is_licensed_module_active(license_data, item) is True


def test_licensed_module_inactive_without_module_or_group_expiry():
    license_data = {"grouplist": {"全平台AI助手": {}}}
    item = {
        "name": "数字员工",
        "module": "digital_employee_bp",
        "auth": True,
        "group": "全平台AI助手",
    }

    assert is_licensed_module_active(license_data, item) is False


def test_licensed_module_active_prefers_module_expiry_when_present():
    license_data = {
        "grouplist": {
            "全平台AI助手": {
                "expireTime": int(time.time() * 1000) - 60000,
            }
        }
    }
    item = {
        "name": "数字员工",
        "module": "digital_employee_bp",
        "auth": True,
        "group": "全平台AI助手",
        "expireTime": int(time.time() * 1000) + 60000,
    }

    assert is_licensed_module_active(license_data, item) is True


def test_asset_management_license_module_matches_menu_description():
    license_data = {
        "grouplist": {
            "资产管理": {
                "expireTime": int(time.time() * 1000) + 60000,
            }
        }
    }
    item = {
        "name": "资产模型",
        "module": "asset_management_bp",
        "auth": True,
        "group": "资产管理",
    }

    assert is_licensed_module_active(license_data, item) is True


def test_network_topology_license_module_uses_network_management_group():
    license_data = {
        "grouplist": {
            "基础功能": {
                "expireTime": int(time.time() * 1000) + 60000,
            },
            "网络管理": {
                "expireTime": int(time.time() * 1000) + 60000,
            },
        }
    }
    item = {
        "name": "网络拓扑",
        "module": "topology_bp",
        "auth": True,
        "group": "网络管理",
    }

    assert is_licensed_module_active(license_data, item) is True


def test_access_control_license_module_matches_network_management_group():
    license_data = {
        "grouplist": {
            "网络管理": {
                "expireTime": int(time.time() * 1000) + 60000,
            }
        }
    }
    item = {
        "name": "访问控制",
        "module": "network_access_control_bp",
        "auth": True,
        "group": "网络管理",
    }

    assert is_licensed_module_active(license_data, item) is True


def test_command_center_license_module_uses_expert_team_group():
    license_data = {
        "grouplist": {
            "专家团队": {
                "expireTime": int(time.time() * 1000) + 60000,
            }
        }
    }
    item = {
        "name": "指挥中心",
        "module": "command_bp",
        "auth": True,
        "group": "专家团队",
    }

    assert is_licensed_module_active(license_data, item) is True


def test_ticket_license_module_uses_ticket_management_group():
    """工单模块的 license 契约：module 键与 aioe_lic.js 的 MODULES_META 一致，
    分组到期时间由 grouplist["工单管理"] 提供（item 上不带 expireTime）。"""
    license_data = {
        "grouplist": {
            "工单管理": {
                "expireTime": int(time.time() * 1000) + 60000,
            }
        }
    }
    item = {
        "name": "工单列表",
        "module": "ticket_bp",
        "auth": True,
        "group": "工单管理",
    }

    assert is_licensed_module_active(license_data, item) is True


def test_ticket_license_module_inactive_when_group_expired():
    """分组过期后工单模块不得激活——否则过期 license 仍能注册蓝图。"""
    license_data = {
        "grouplist": {
            "工单管理": {
                "expireTime": int(time.time() * 1000) - 60000,
            }
        }
    }
    item = {
        "name": "工单列表",
        "module": "ticket_bp",
        "auth": True,
        "group": "工单管理",
    }

    assert is_licensed_module_active(license_data, item) is False


def test_ticket_bp_is_registered_in_licensed_blueprint_map():
    """license 里声明了 ticket_bp 也必须在 _licensed_blueprints() 里有对应项，
    否则 _register_licensed_blueprints 会在 module not in bp_dict 处静默跳过。"""
    from services.app_factory import _licensed_blueprints

    bp_dict = _licensed_blueprints()
    assert "ticket_bp" in bp_dict
    assert getattr(bp_dict["ticket_bp"], "name", None) == "ticket"
