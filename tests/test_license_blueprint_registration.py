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
