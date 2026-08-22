# coding=utf-8
import orjson
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs import kafka
from services.workflow.input_resolution import (
    merge_and_validate_flow_input,
    normalize_flow_task_message,
    playbook_input_schema,
)


START_ID = "c8117702-fcaf-4eaf-86fa-425c561c3fe1"


def _flow_json():
    return {
        "nodes": [
            {"id": "action-first", "type": "action-node", "properties": {}},
            {
                "id": START_ID,
                "type": "start-node",
                "properties": {
                    "start_params": [
                        {"name": "start_time", "type": "string", "desc": "开始时间", "default": "2026-01-01"},
                        {"name": "window", "type": "object", "desc": "时间窗口", "default": {"end": "2026-01-31"}},
                        {"name": "optional", "type": "string", "desc": "可选参数", "default": None},
                        {"name": "unused", "type": "string", "desc": "未使用", "default": "ignored"},
                    ]
                },
            },
            {
                "id": "nested",
                "type": "action-node",
                "properties": {
                    "input": {
                        "start": f"${{{START_ID}.output.start_time}}",
                        "values": [
                            {"end": f"prefix-${{{START_ID}.output.window.end}}"},
                            f"${{{START_ID}.output.optional}}",
                        ],
                    }
                },
            },
        ],
        "edges": [],
    }


def test_merge_defaults_with_caller_input_and_validate_nested_references():
    result = merge_and_validate_flow_input(
        _flow_json(),
        {"start_time": "2026-02-01", "optional": "", "extra": True},
    )

    assert result == {
        "start_time": "2026-02-01",
        "window": {"end": "2026-01-31"},
        "optional": "",
        "unused": "ignored",
        "extra": True,
    }


@pytest.mark.parametrize(
    "caller_input",
    [
        {},
        {"optional": "", "window": {}},
        {"optional": "", "window": {"end": None}},
        [],
    ],
)
def test_invalid_or_missing_referenced_input_is_rejected(caller_input):
    with pytest.raises(CustomError) as exc_info:
        merge_and_validate_flow_input(_flow_json(), caller_input)

    assert exc_info.value.enum == ErrorCode.PLAYBOOK_INPUT_ERROR


def test_playbook_input_schema_uses_start_node_and_includes_defaults():
    assert playbook_input_schema(_flow_json()) == [
        {"start_time": {"type": "string", "description": "开始时间", "default": "2026-01-01"}},
        {"window": {"type": "object", "description": "时间窗口", "default": {"end": "2026-01-31"}}},
        {"optional": {"type": "string", "description": "可选参数", "default": None}},
    ]


@pytest.mark.asyncio
async def test_normalize_flow_task_message_updates_flow_input_without_mutating_caller():
    class FakePlaybookVersion:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs == {"id": "flow-1", "tenant_id": "tenant-1"}
            return type("Playbook", (), {"flow_json": _flow_json()})()

    message = {
        "run_type": "flow",
        "run_args": {"flow_id": "flow-1", "flow_input": {"optional": ""}},
        "tenant_id": "tenant-1",
    }
    normalized = await normalize_flow_task_message(message, FakePlaybookVersion)

    assert normalized["run_args"]["flow_input"]["start_time"] == "2026-01-01"
    assert normalized["run_args"]["flow_input"]["window"] == {"end": "2026-01-31"}
    assert message["run_args"]["flow_input"] == {"optional": ""}


@pytest.mark.asyncio
async def test_normalize_flow_task_message_supports_default_tenant_internal_playbook():
    calls = []

    class FakePlaybookVersion:
        @classmethod
        async def get_or_none(cls, **kwargs):
            calls.append(kwargs)
            if kwargs["tenant_id"] == "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18":
                return type("Playbook", (), {"flow_json": _flow_json()})()
            return None

    normalized = await normalize_flow_task_message(
        {
            "run_type": "flow",
            "run_args": {"flow_id": "flow-1", "flow_input": {"optional": ""}},
            "tenant_id": "tenant-1",
        },
        FakePlaybookVersion,
    )

    assert [call["tenant_id"] for call in calls] == [
        "tenant-1",
        "8bce552d-c27c-41d4-8a1b-8c5bf38dfe18",
    ]
    assert normalized["run_args"]["flow_input"]["start_time"] == "2026-01-01"


class _FakeProducer:
    created = 0
    sent = []
    configs = []

    def __init__(self, **kwargs):
        type(self).created += 1
        type(self).configs.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send_and_wait(self, topic, value, key, partition):
        type(self).sent.append((topic, orjson.loads(value), key, partition))


@pytest.mark.asyncio
async def test_aproduce_normalizes_flow_before_creating_producer(monkeypatch):
    _FakeProducer.created = 0
    _FakeProducer.sent = []
    _FakeProducer.configs = []
    normalized = {"run_type": "flow", "run_args": {"flow_input": {"from_default": True}}}

    async def fake_normalize(message):
        assert message["run_args"]["flow_input"] == {}
        return normalized

    monkeypatch.setattr(kafka, "TASK_TOPIC", "task-topic")
    monkeypatch.setattr(kafka, "_normalize_flow_task_message", fake_normalize)
    monkeypatch.setattr(kafka, "AIOKafkaProducer", _FakeProducer)

    await kafka.aproduce(
        "task-topic",
        {"run_type": "flow", "run_args": {"flow_input": {}}},
    )

    assert _FakeProducer.created == 1
    assert _FakeProducer.configs[0]["enable_idempotence"] is True
    assert _FakeProducer.sent[0][1] == normalized


@pytest.mark.asyncio
async def test_aproduce_propagates_validation_error_before_creating_producer(monkeypatch):
    _FakeProducer.created = 0

    async def reject(_message):
        raise CustomError(ErrorCode.PLAYBOOK_INPUT_ERROR)

    monkeypatch.setattr(kafka, "TASK_TOPIC", "task-topic")
    monkeypatch.setattr(kafka, "_normalize_flow_task_message", reject)
    monkeypatch.setattr(kafka, "AIOKafkaProducer", _FakeProducer)

    with pytest.raises(CustomError):
        await kafka.aproduce("task-topic", {"run_type": "flow", "run_args": {}})

    assert _FakeProducer.created == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {"run_type": "action", "run_args": {}},
        {"run_type": "flow", "run_args": {}, "timer": "remove"},
    ],
)
async def test_aproduce_bypasses_non_execution_messages(monkeypatch, message):
    _FakeProducer.created = 0
    _FakeProducer.sent = []

    async def unexpected(_message):
        raise AssertionError("normalizer should not be called")

    monkeypatch.setattr(kafka, "TASK_TOPIC", "task-topic")
    monkeypatch.setattr(kafka, "_normalize_flow_task_message", unexpected)
    monkeypatch.setattr(kafka, "AIOKafkaProducer", _FakeProducer)

    await kafka.aproduce("task-topic", message)

    assert _FakeProducer.created == 1
    assert _FakeProducer.configs[-1]["enable_idempotence"] is True
    assert _FakeProducer.sent[0][1] == message


@pytest.mark.asyncio
async def test_aproduce_does_not_change_non_task_producer_config(monkeypatch):
    _FakeProducer.created = 0
    _FakeProducer.sent = []
    _FakeProducer.configs = []

    monkeypatch.setattr(kafka, "TASK_TOPIC", "task-topic")
    monkeypatch.setattr(kafka, "AIOKafkaProducer", _FakeProducer)

    await kafka.aproduce("audit-topic", {"type": "audit"})

    assert _FakeProducer.created == 1
    assert "enable_idempotence" not in _FakeProducer.configs[0]
