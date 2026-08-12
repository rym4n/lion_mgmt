# coding=utf-8
import orjson
import pytest

from services.command_center import realtime


@pytest.mark.asyncio
async def test_publish_command_center_update_broadcasts_only_dirty_event(monkeypatch):
    calls = []

    class DummyPublisher:
        async def publish(self, topic, payload):
            calls.append((topic, payload))

    monkeypatch.setattr(realtime, "WR_TOPIC", "warroom-topic")
    monkeypatch.setattr(realtime.chat_manager, "msg_pub", DummyPublisher())

    await realtime.publish_command_center_update("tenant-1", "task_created", "task-1")

    assert calls == [
        ("warroom-topic", orjson.dumps({"type": "command_center_updated"}).decode())
    ]
