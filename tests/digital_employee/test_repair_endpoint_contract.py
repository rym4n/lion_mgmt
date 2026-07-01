# coding=utf-8
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VIEWS_PATH = ROOT / "services/digital_employee/views.py"


def _function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(VIEWS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_public_employee_repair_no_longer_performs_manual_workspace_sync():
    node = _function("employee_repair")
    called = _called_names(node)

    assert "sync_employee_workspace" not in called
    assert "_repair_employee_workspace" not in called
    assert any(isinstance(child, ast.Raise) for child in ast.walk(node))


def test_internal_employee_repair_keeps_token_gated_repair_path():
    node = _function("internal_employee_repair")
    called = _called_names(node)

    assert "_check_internal" in called
    assert "_repair_employee_workspace" in called
