import ast
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
