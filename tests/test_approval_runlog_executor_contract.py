# coding=utf-8
import ast
from pathlib import Path

from libs.chat import ChatManager


ROOT = Path(__file__).resolve().parents[1]
APPROVEMENT_VIEWS_PATH = ROOT / "services/approvement/views.py"


def _function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(APPROVEMENT_VIEWS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _executor_values(node: ast.AST):
    values = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Dict):
            continue
        for key, value in zip(child.keys, child.values):
            if isinstance(key, ast.Constant) and key.value == "executor":
                values.append(value)
    return values


def test_approval_update_executor_is_single_actor_not_approver_list():
    values = _executor_values(_function("update_approvement"))

    assert [ast.unparse(value) for value in values] == ["_approval_executor(account_info)"]
    assert all('",".join' not in ast.unparse(value) and "','.join" not in ast.unparse(value) for value in values)


def test_approval_callback_executor_is_not_joined_approver_list():
    values = _executor_values(_function("update_approvement_callback"))

    assert [ast.unparse(value) for value in values] == ["_callback_executor(approve)"]
    assert all('",".join' not in ast.unparse(value) and "','.join" not in ast.unparse(value) for value in values)


def test_runlog_executor_normalizer_keeps_single_id_fields_valid():
    first = "af87bc4d-02ec-49e5-8dd5-247069cc7616"
    second = "43782755-6382-4f42-82a2-5e557370d858"
    log = {"executor": f"{first},{second}", "args": {}}

    ChatManager._normalize_runlog_executor(log)

    assert log["executor"] == first
    assert log["args"]["raw_executor"] == f"{first},{second}"
