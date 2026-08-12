# coding=utf-8
from types import SimpleNamespace

import pytest

from services.digital_employee.lion_agent_client import LionAgentExecutionError
from services.inspection import logic


def _target() -> SimpleNamespace:
    return SimpleNamespace(
        id="target-1",
        content="获取性能指标",
        standard="平均利用率小于等于 80",
    )


def test_large_result_is_structurally_sampled_within_input_budget():
    rows = [
        {
            "instance_id": f"instance-{index}",
            "metrics": [
                {"name": "cpu", "value": index},
                {"name": "memory", "value": index + 1},
            ],
            "description": "x" * 1000,
        }
        for index in range(5000)
    ]

    message, sampled, original_chars = logic._build_judgment_rule_message(
        _target(),
        {"result": {"data": rows}},
    )

    assert sampled is True
    assert original_chars > 1_000_000
    assert logic._estimate_input_tokens(message) <= logic.INSPECTION_RULE_INPUT_MAX_TOKENS
    assert "instance-0" in message
    assert "instance-2500" in message
    assert "instance-4999" in message
    assert "以下仅保留字段结构和代表性数组项" in message


def test_small_result_is_sent_without_sampling():
    result = {"result": {"cpu": 42, "status": "ok"}}

    message, sampled, original_chars = logic._build_judgment_rule_message(_target(), result)

    assert sampled is False
    assert original_chars == len(logic._json_text(result))
    assert '"cpu": 42' in message


@pytest.mark.asyncio
async def test_non_retryable_context_error_stops_rule_generation(monkeypatch):
    calls = 0

    async def fail_once(**_kwargs):
        nonlocal calls
        calls += 1
        raise LionAgentExecutionError(
            "context_window_exceeded",
            "模型输入超过上下文窗口",
            retryable=False,
        )

    async def resolve_llm(_tenant_id):
        return SimpleNamespace(model_name="aioe", to_payload=lambda: {})

    monkeypatch.setattr(logic, "execute_prompt", fail_once)
    monkeypatch.setattr("services.llm_config.resolver.resolve_llm", resolve_llm)

    with pytest.raises(ValueError, match="context_window_exceeded"):
        await logic._generate_inspection_judgment_rule(
            _target(),
            {"result": {"cpu": 42}},
            "tenant-1",
        )

    assert calls == 1
