# coding=utf-8
"""数字员工的知识空间绑定（SPEC §7.3）。

本仓测试不连数据库，模型层断言直接读 Tortoise 字段描述对象。
"""

import importlib
import sys
from pathlib import Path

import pytest

from libs.error_code.exception import CustomError


_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PREFIXES = (
    "core",
    "croniter",
    "libs",
    "models",
    "sanic",
    "services",
    "settings",
    "tortoise",
)


def _is_scoped(name: str) -> bool:
    return any(name == prefix or name.startswith(f"{prefix}.") for prefix in _MODULE_PREFIXES)


def _load_real_module_snapshot() -> dict[str, object]:
    """Capture the real application modules before stub-heavy tests run."""
    old_path = sys.path.copy()
    try:
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        for name in _MODULE_PREFIXES:
            importlib.import_module(name)
        for name in (
            "core.error_code",
            "core.response",
            "libs.error_code.exception",
            "models.command_center",
            "models.digital_employee",
            "models.knowledge",
            "sanic.log",
        ):
            importlib.import_module(name)
        return {name: module for name, module in sys.modules.items() if _is_scoped(name)}
    finally:
        sys.path[:] = old_path


_REAL_MODULES = _load_real_module_snapshot()


def _restore_real_modules() -> None:
    """Discard stubs/new imports and restore the captured module graph."""
    for name in list(sys.modules):
        if _is_scoped(name) and name not in _REAL_MODULES:
            sys.modules.pop(name, None)
    sys.modules.update(_REAL_MODULES)


@pytest.fixture(autouse=True)
def _isolate_knowledge_imports():
    _restore_real_modules()
    yield
    _restore_real_modules()


def _import_agent():
    root = Path(__file__).resolve().parents[2]
    old_path = sys.path.copy()
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        sys.modules.pop("models", None)
        sys.modules.pop("models.command_center", None)
        from models.command_center import CommandAgent

        return CommandAgent
    finally:
        sys.path[:] = old_path


def test_knowledge_space_keys_defaults_to_empty_list():
    """默认不绑任何空间——没配过的员工不该意外获得知识访问权。"""
    CommandAgent = _import_agent()
    field = CommandAgent._meta.fields_map["knowledge_space_keys"]
    assert field.default == list


def test_knowledge_write_space_nullable():
    """不配可写空间是常态，此时员工没有写入工具。"""
    CommandAgent = _import_agent()
    assert CommandAgent._meta.fields_map["knowledge_write_space"].null is True


def test_write_space_must_be_subset_of_visible():
    """可写空间必须是可见空间之一。"""
    from services.digital_employee.manager import validate_knowledge_binding

    validate_knowledge_binding(["ops", "runbook"], "runbook")
    validate_knowledge_binding(["ops"], None)

    with pytest.raises(CustomError):
        validate_knowledge_binding(["ops"], "runbook")

    with pytest.raises(CustomError):
        validate_knowledge_binding([], "ops")


def test_binding_rejects_non_list():
    from services.digital_employee.manager import validate_knowledge_binding

    with pytest.raises(CustomError):
        validate_knowledge_binding("ops", None)
