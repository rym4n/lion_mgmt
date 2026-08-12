import ast
import asyncio
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function(path: Path, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text())
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def test_new_tenant_admin_uses_platform_wildcard_permissions():
    function = _function(ROOT / "services/tenant/views.py", "add_tenant")
    role_create = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "create"
        and any(keyword.arg == "permissions" for keyword in node.value.keywords)
    )
    permissions = next(
        keyword.value
        for keyword in role_create.value.keywords
        if keyword.arg == "permissions"
    )

    assert ast.literal_eval(permissions) == ["*:*"]


def test_playbook_fallback_tenant_admin_uses_platform_wildcard_permissions():
    function = _function(ROOT / "services/workflow/views.py", "create_workflow")
    role_create = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "create"
        and any(keyword.arg == "permissions" for keyword in node.value.keywords)
    )
    permissions = next(
        keyword.value
        for keyword in role_create.value.keywords
        if keyword.arg == "permissions"
    )

    assert ast.literal_eval(permissions) == ["*:*"]


def test_workflow_import_uses_refined_tool_upload_permission():
    function = _function(ROOT / "services/workflow/views.py", "import_workflow")
    decorator_names = [
        decorator.args[0].id
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "requires_permission"
        and decorator.args
        and isinstance(decorator.args[0], ast.Name)
    ]

    assert "TOOL_UPLOAD_PERMISSION" in decorator_names


def test_existing_tenant_admin_roles_are_promoted_to_wildcard_permissions():
    migration = importlib.import_module(
        "migrations.models.78_20260717173000_promote_tenant_admin_permissions"
    )
    upgrade_sql = asyncio.run(migration.upgrade(None))

    assert '"name" = \'租户管理员\'' in upgrade_sql
    assert '"permissions" = \'["*:*\"]\'::jsonb' in upgrade_sql
    assert '"deleted" = False' in upgrade_sql
    assert "? '*:*'" in upgrade_sql
