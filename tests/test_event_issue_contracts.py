# coding=utf-8
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_node(path: str, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def test_event_update_treats_an_explicit_empty_summary_as_an_update():
    function = _function_node("services/event/views.py", "update_event")

    summary_guard = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Constant)
        and node.left.value == "event_summary"
        and any(isinstance(operator, ast.In) for operator in node.ops)
    )

    assert isinstance(summary_guard.comparators[0], ast.Name)
    assert summary_guard.comparators[0].id == "params"


def test_log_index_list_excludes_soft_deleted_event_ingestions():
    function = _function_node("services/log_view/views.py", "get_index_list")

    filters = [
        keyword
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "filter"
        for keyword in node.keywords
    ]

    deleted_filter = next(keyword for keyword in filters if keyword.arg == "deleted")
    assert isinstance(deleted_filter.value, ast.Constant)
    assert deleted_filter.value.value is False
