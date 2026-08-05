# coding=utf-8
from types import SimpleNamespace

from services.timed.views import _canonicalize_timed_action_input


def test_timed_action_snapshot_is_derived_from_selected_action_and_tool():
    action = SimpleNamespace(
        action={"func": "health_check", "name": "检查资产是否健康"}
    )
    tool = SimpleNamespace(
        name="topsec_ngfw_ssh",
        version="v1",
        main="topsec_ngfw.py",
        description="天融信ngfw",
    )
    submitted = {
        "app_name": "spoofed-tool",
        "app_ver": "spoofed-version",
        "main": "spoofed.py",
        "name": "spoofed-name",
        "node_name": "解封ip",
        "action_name": "解封ip",
        "action": "unblock_ip",
        "action_input": {"target": "asset-1"},
        "resource": "resource-1",
        "retry_times": 2,
        "timeout": 120,
    }

    canonical = _canonicalize_timed_action_input(
        action,
        tool,
        submitted,
        "account-1",
    )

    assert canonical == {
        "app_name": "topsec_ngfw_ssh",
        "app_ver": "v1",
        "main": "topsec_ngfw.py",
        "name": "天融信ngfw",
        "node_name": "检查资产是否健康",
        "action_name": "检查资产是否健康",
        "action": "health_check",
        "action_input": {"target": "asset-1"},
        "resource": "resource-1",
        "retry_times": 2,
        "timeout": 120,
        "executor": "account-1",
    }
    assert submitted["action_name"] == "解封ip"
