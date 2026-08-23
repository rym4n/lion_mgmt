# coding=utf-8
"""Keep knowledge tests independent from tests that install module stubs.

Several older tests load implementation files with lightweight replacements in
``sys.modules`` (notably ``models``, ``services`` and ``tortoise``).  Those
replacements are intentionally local to the test, but a few helpers leave them
behind.  Knowledge tests import the real application modules lazily, so a
leftover stub changes the import contract before the test body runs.
"""

import importlib
import sys
from pathlib import Path

import pytest


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
    """Import the real roots before test helpers can replace them."""
    old_path = sys.path.copy()
    try:
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        for name in _MODULE_PREFIXES:
            importlib.import_module(name)
        # These are imported by knowledge services and by the test modules'
        # top-level imports; snapshotting them preserves class identity.
        for name in (
            "core.error_code",
            "core.response",
            "libs.error_code.exception",
            "models.command_center",
            "models.digital_employee",
            "models.knowledge",
            "models.openviking",
            "sanic.log",
        ):
            importlib.import_module(name)
        return {name: module for name, module in sys.modules.items() if _is_scoped(name)}
    finally:
        sys.path[:] = old_path


_REAL_MODULES = _load_real_module_snapshot()


def _restore_real_modules() -> None:
    """Discard stubs/new imports and put the real module graph back."""
    for name in list(sys.modules):
        if _is_scoped(name) and name not in _REAL_MODULES:
            sys.modules.pop(name, None)
    sys.modules.update(_REAL_MODULES)


@pytest.fixture(autouse=True)
def _isolate_knowledge_imports():
    _restore_real_modules()
    yield
    _restore_real_modules()
