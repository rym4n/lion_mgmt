# coding=utf-8
"""调用点改造契约：确认三条链路不再读环境变量。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_ENV_NAMES = ("RY_AI_MODEL", "RY_AI_API_BASE", "LLM_KEY", "RY_AI_LITE_MODEL")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_inspection_no_longer_reads_llm_env():
    src = _read("services/inspection/logic.py")
    for name in FORBIDDEN_ENV_NAMES:
        assert name not in src, f"inspection 仍在引用 {name}，应改用 resolve_llm"
    assert "resolve_llm" in src


def test_workflow_ai_chat_no_longer_reads_llm_env():
    src = _read("services/workflow/views.py")
    for name in FORBIDDEN_ENV_NAMES:
        assert name not in src, f"workflow 仍在引用 {name}，应改用 resolve_llm"
    assert "resolve_llm" in src


def test_dispatcher_uses_lite_purpose():
    src = _read("services/command_center/dispatcher.py")
    assert "RY_AI_LITE_MODEL" not in src
    assert 'purpose="lite"' in src


def test_settings_marks_llm_env_deprecated():
    """settings 里的定义可保留但必须标注弃用，避免后人误用。"""
    src = _read("settings/__init__.py")
    assert "RY_AGENT_MODEL" not in src, "确认无消费者的死配置应清理"
