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


def test_security_management_license_module_matches_menu_description():
    license_data = {
        "grouplist": {
            "安全管理": {
                "expireTime": int(time.time() * 1000) + 60000,
            }
        }
    }
    item = {
        "name": "安全管理",
        "module": "supply_chain_review_bp",
        "auth": True,
        "group": "安全管理",
    }

    assert is_licensed_module_active(license_data, item) is True
