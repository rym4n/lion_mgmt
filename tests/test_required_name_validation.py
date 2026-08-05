# coding=utf-8
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.utils import normalize_required_text
from models.timed import Timed
from services.asset_management import manager


ROOT = Path(__file__).resolve().parents[1]


def _function(path: str, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _calls(function: ast.AsyncFunctionDef, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
        for node in ast.walk(function)
    )


def test_normalize_required_text_trims_edges_and_preserves_inner_spaces():
    assert normalize_required_text("  安全 运维  ") == "安全 运维"


@pytest.mark.parametrize("value", [None, 1, "", " \t\n", "\u200b", "名称\u2060"])
def test_normalize_required_text_rejects_missing_or_whitespace_only(value):
    with pytest.raises(CustomError) as exc_info:
        normalize_required_text(value)

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_model_write_boundary_rejects_noncanonical_or_invisible_names():
    valid = Timed(name="安全 运维", type=1, status=0, tactics="now", end_time="")
    valid._validate_persisted_name()

    for name in ("  安全运维  ", "\u200b"):
        timed = Timed(name=name, type=1, status=0, tactics="now", end_time="")
        with pytest.raises(CustomError) as exc_info:
            timed._validate_persisted_name()
        assert exc_info.value.enum is ErrorCode.PARAM_ERROR


def test_required_dynamic_text_field_rejects_whitespace_only():
    field = SimpleNamespace(
        data_type="text",
        required=True,
        options={},
    )

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(manager.validate_field_value("tenant-a", field, "   "))

    assert exc_info.value.enum is ErrorCode.PARAM_ERROR


@pytest.mark.parametrize(
    ("path", "function_name"),
    [
        ("services/event/views.py", "artificial_create_event"),
        ("services/event/views.py", "artificial_create_event_wuzhi"),
        ("services/event/views.py", "create_event"),
        ("services/event/views.py", "update_event"),
        ("services/workflow/views.py", "create_workflow"),
        ("services/workflow/views.py", "edit_workflow"),
        ("services/timed/views.py", "add_timed"),
    ],
)
def test_name_write_paths_use_required_trimmed_text(path, function_name):
    assert _calls(_function(path, function_name), "normalize_required_text")
