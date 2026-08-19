# coding=utf-8
"""RY_MEMORY_BACKEND 开关契约（SPEC §8 阶段一 1A 第 2 步）。

三条语义各自可判定：默认 reme、显式 cognee、未知值 fail-closed。
"""
import importlib
import os
import sys

import pytest

# 确保当前目录在 sys.path 中
if "." not in sys.path:
    sys.path.insert(0, ".")


@pytest.fixture
def reload_settings(monkeypatch):
    """在受控 env 下重新加载 settings，并在用例结束后还原为进程原始状态。"""
    original_value = os.environ.get("RY_MEMORY_BACKEND")

    # 保存 settings 模块的原始状态
    original_settings = sys.modules.get("settings")

    def _reload(value: str | None):
        if value is None:
            monkeypatch.delenv("RY_MEMORY_BACKEND", raising=False)
        else:
            monkeypatch.setenv("RY_MEMORY_BACKEND", value)

        # 移除已缓存的 settings 模块
        if "settings" in sys.modules:
            del sys.modules["settings"]

        # 重新导入
        import settings
        return settings

    yield _reload

    # 还原环境变量
    if original_value is None:
        monkeypatch.delenv("RY_MEMORY_BACKEND", raising=False)
    else:
        monkeypatch.setenv("RY_MEMORY_BACKEND", original_value)

    # 还原 settings 模块
    if "settings" in sys.modules:
        del sys.modules["settings"]
    if original_settings is not None:
        sys.modules["settings"] = original_settings


def test_backend_defaults_to_reme(reload_settings):
    reloaded = reload_settings(None)
    assert reloaded.MEMORY_BACKEND == "reme"
    assert reloaded.MEMORY_BACKEND == reloaded.MEMORY_BACKEND_REME


def test_backend_accepts_explicit_cognee(reload_settings):
    reloaded = reload_settings("cognee")
    assert reloaded.MEMORY_BACKEND == "cognee"
    assert reloaded.MEMORY_BACKEND == reloaded.MEMORY_BACKEND_COGNEE


def test_backend_normalizes_case_and_whitespace(reload_settings):
    reloaded = reload_settings("  ReMe  ")
    assert reloaded.MEMORY_BACKEND == "reme"


def test_unknown_backend_fails_closed_at_import(reload_settings):
    """未知值必须让进程起不来，绝不静默回落默认值。"""
    with pytest.raises(ValueError) as exc_info:
        reload_settings("redis")
    message = str(exc_info.value)
    assert "RY_MEMORY_BACKEND" in message
    assert "cognee" in message and "reme" in message


def test_empty_string_fails_closed(reload_settings):
    """空串是「配了但配错」，不是「没配」，同样 fail-closed。"""
    with pytest.raises(ValueError):
        reload_settings("")


def test_allowed_set_is_exactly_two_values():
    import settings
    assert settings.MEMORY_BACKENDS == frozenset({"cognee", "reme"})
