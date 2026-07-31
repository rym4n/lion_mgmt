# coding=utf-8
import ast
from pathlib import Path

from core.maintenance import (
    DEFAULT_TENANT_ID,
    can_enable_maintenance_mode,
    can_switch_tenant,
    should_clear_maintenance_mode,
)


ROOT = Path(__file__).resolve().parents[1]


def _function_source(path, name):
    source = path.read_text()
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    return ast.get_source_segment(source, function)


def test_only_default_tenant_admin_can_enable_maintenance_mode():
    assert can_enable_maintenance_mode("admin", DEFAULT_TENANT_ID)
    assert not can_enable_maintenance_mode("admin", "tenant-other")
    assert not can_enable_maintenance_mode("operator", DEFAULT_TENANT_ID)


def test_non_default_tenant_requires_maintenance_cleanup():
    assert should_clear_maintenance_mode("tenant-other")
    assert not should_clear_maintenance_mode(DEFAULT_TENANT_ID)


def test_maintenance_mode_blocks_tenant_switching():
    assert can_switch_tenant(False)
    assert not can_switch_tenant(True)


def test_system_endpoint_uses_the_maintenance_tenant_guard():
    function = _function_source(
        ROOT / "services/system_config/views.py",
        "update_maintenance_mode_status",
    )

    assert "can_enable_maintenance_mode(" in function
    assert "raise CustomError(ErrorCode.MAINTENANCE_MODE_ERROR)" in function


def test_tenant_switch_clears_non_default_maintenance_state():
    helper = _function_source(
        ROOT / "services/tenant/views.py",
        "_clear_maintenance_mode_for_non_default_tenant",
    )
    switch = _function_source(
        ROOT / "services/tenant/views.py",
        "switch_tenant",
    )

    assert "should_clear_maintenance_mode(tenant_id)" in helper
    assert "cache_client.set(" in helper
    assert "False" in helper
    assert "_clear_maintenance_mode_for_non_default_tenant(" in switch
    assert "can_switch_tenant(maintenance_mode)" in switch
    assert "raise CustomError(ErrorCode.MAINTENANCE_MODE_ERROR)" in switch
    maintenance_guard_position = switch.index("can_switch_tenant(maintenance_mode)")
    tenant_lookup_position = switch.index("Tenant.get_or_none")
    assert maintenance_guard_position < tenant_lookup_position
    cleanup_position = switch.index(
        "_clear_maintenance_mode_for_non_default_tenant("
    )
    tenant_change_position = switch.index(
        'request.ctx.session["tenant_id"] = tenant.id'
    )
    assert cleanup_position < tenant_change_position
