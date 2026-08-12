# coding=utf-8

import pytest

from services.workflow.views import _normalize_ai_chat_payload


@pytest.mark.parametrize(
    "forced_choice",
    [
        "required",
        "none",
        "AgentOutput",
        {"type": "function", "function": {"name": "AgentOutput"}},
    ],
)
def test_ai_chat_payload_replaces_forced_tool_choice(forced_choice):
    payload = {
        "model": "thinking-model",
        "tools": [{"type": "function", "function": {"name": "AgentOutput"}}],
        "tool_choice": forced_choice,
    }

    normalized = _normalize_ai_chat_payload(payload)

    assert normalized["tools"] == payload["tools"]
    assert normalized["tool_choice"] == "auto"
    assert payload["tool_choice"] == forced_choice


def test_ai_chat_payload_omits_tool_fields_without_tools():
    normalized = _normalize_ai_chat_payload(
        {"model": "thinking-model", "tools": [], "tool_choice": "none"},
    )

    assert "tools" not in normalized
    assert "tool_choice" not in normalized


def test_ai_chat_payload_preserves_omitted_tool_choice():
    normalized = _normalize_ai_chat_payload(
        {
            "model": "thinking-model",
            "tools": [{"type": "function", "function": {"name": "AgentOutput"}}],
        },
    )

    assert "tool_choice" not in normalized
