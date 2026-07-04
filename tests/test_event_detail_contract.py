# coding=utf-8
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEWS_PATH = ROOT / "services/event/views.py"


def _function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(VIEWS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _is_initialize_room_guard(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "initialize_room"


def _calls_attr(node: ast.AST, attr: str) -> bool:
    return any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == attr for child in ast.walk(node))


def test_event_detail_defaults_to_read_only():
    node = _function("get_event_detail")

    guarded_calls = []
    unguarded_calls = []
    for statement in node.body:
        if isinstance(statement, ast.If) and _is_initialize_room_guard(statement.test):
            guarded_calls.extend(statement.body)
            continue
        if _calls_attr(statement, "save") or _calls_attr(statement, "add"):
            unguarded_calls.append(statement)

    assert unguarded_calls == []
    assert any(_calls_attr(statement, "save") for statement in guarded_calls)
    assert sum(1 for statement in guarded_calls if _calls_attr(statement, "add")) == 2


def test_event_detail_requires_explicit_room_initialization_flag():
    node = _function("get_event_detail")

    assignments = [statement for statement in node.body if isinstance(statement, ast.Assign)]
    initialize_room_assignment = next(
        statement
        for statement in assignments
        if any(isinstance(target, ast.Name) and target.id == "initialize_room" for target in statement.targets)
    )

    assert isinstance(initialize_room_assignment.value, ast.Call)
    assert isinstance(initialize_room_assignment.value.func, ast.Name)
    assert initialize_room_assignment.value.func.id == "bool"
    assert ast.unparse(initialize_room_assignment.value.args[0]) == "request.json.get('initialize_room')"
