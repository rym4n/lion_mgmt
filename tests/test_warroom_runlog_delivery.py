# coding=utf-8
from types import SimpleNamespace

import orjson
import pytest

from libs.chat import ChatManager
from services.warroom import views


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(orjson.loads(payload))


@pytest.mark.asyncio
async def test_runlog_delivery_falls_back_to_executor_when_room_context_is_stale(monkeypatch):
    manager = ChatManager.__new__(ChatManager)
    manager.redis = None
    published = []
    created = []

    async def create_message(**kwargs):
        created.append(kwargs)
        return {
            "type": "runlog",
            "room_id": kwargs["room_id"],
            "content": [kwargs["log"]],
        }

    async def broadcast(payload, **kwargs):
        published.append((orjson.loads(payload), kwargs))

    monkeypatch.setattr("libs.chat.creat_message", create_message)
    monkeypatch.setattr(manager, "_broadcast", broadcast)

    await manager._dispatch_message(
        {
            "type": "message",
            "data": orjson.dumps(
                {
                    "room_id": "room-1",
                    "tenant_id": "tenant-1",
                    "updator": "user-1",
                    "log": {
                        "run_id": "run-1",
                        "node_id": "node-1",
                        "executor": "user-1",
                    },
                }
            ),
        }
    )

    assert created[0]["tenant_id"] == "tenant-1"
    assert published == [
        (
            {
                "type": "runlog",
                "room_id": "room-1",
                "content": [
                    {
                        "run_id": "run-1",
                        "node_id": "node-1",
                        "executor": "user-1",
                    }
                ],
            },
            {
                "room_id": "room-1",
                "extra_user_id": "user-1",
                "tenant_id": "tenant-1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_room_runlog_executor_fallback_respects_tenant():
    manager = ChatManager.__new__(ChatManager)
    current_room = FakeWebSocket()
    same_tenant_executor = FakeWebSocket()
    other_tenant_executor = FakeWebSocket()
    manager.active_connections = {
        "room-user": {
            "room-session": {
                "conn": current_room,
                "current_room": "room-1",
                "tenant_id": "tenant-1",
            }
        },
        "user-1": {
            "same-tenant-session": {
                "conn": same_tenant_executor,
                "current_room": None,
                "tenant_id": "tenant-1",
            },
            "other-tenant-session": {
                "conn": other_tenant_executor,
                "current_room": None,
                "tenant_id": "tenant-2",
            },
        },
    }

    await manager._broadcast(
        orjson.dumps({"type": "runlog"}).decode(),
        room_id="room-1",
        extra_user_id="user-1",
        tenant_id="tenant-1",
    )

    assert len(current_room.sent) == 1
    assert len(same_tenant_executor.sent) == 1
    assert other_tenant_executor.sent == []


@pytest.mark.asyncio
async def test_command_center_runtime_event_is_tenant_scoped_and_normalized(monkeypatch):
    manager = ChatManager.__new__(ChatManager)
    same_tenant = FakeWebSocket()
    other_tenant = FakeWebSocket()
    manager.active_connections = {
        "same-user": {
            "session": {
                "conn": same_tenant,
                "current_room": None,
                "tenant_id": "tenant-1",
            }
        },
        "other-user": {
            "session": {
                "conn": other_tenant,
                "current_room": None,
                "tenant_id": "tenant-2",
            }
        },
    }

    await manager._dispatch_message(
        {
            "type": "message",
            "data": orjson.dumps(
                {
                    "type": "command_center_runtime",
                    "task_id": "task-1",
                    "tenant_id": "tenant-1",
        "receiver_id": "same-user",
                    "stream_id": "digital-employee:task-1",
                    "event_type": "thinking",
                    "content": "正在分析",
                }
            ),
        }
    )

    assert same_tenant.sent == [
        {
            "type": "command_center_updated",
            "runtime": {
                "task_id": "task-1",
                "tenant_id": "tenant-1",
                "receiver_id": "same-user",
                "stream_id": "digital-employee:task-1",
                "event_type": "thinking",
                "content": "正在分析",
            },
        }
    ]
    assert other_tenant.sent == []


@pytest.mark.asyncio
async def test_legacy_runlog_history_rebuilds_missing_message_index(monkeypatch):
    runlogs = [
        SimpleNamespace(run_id="run-2", ctime="2026-08-24 10:00:00"),
        SimpleNamespace(run_id="run-1", ctime="2026-08-24 09:00:00"),
    ]

    class FakeRunlogQuery:
        def filter(self, **kwargs):
            assert kwargs == {
                "run_id__in": ["run-1", "run-2"],
                "tenant_id": "tenant-1",
                "deleted": False,
            }
            return self

        def order_by(self, *fields):
            assert fields == ("ctime", "id")
            return self

        async def all(self):
            return runlogs

    class FakeRunlog:
        @classmethod
        def filter(cls, **kwargs):
            return FakeRunlogQuery().filter(**kwargs)

    class FakeAccount:
        @classmethod
        async def get_or_none(cls, **kwargs):
            assert kwargs == {"username": "system"}
            return SimpleNamespace(id="system-1", username="system", display_name="系统")

    async def summaries(*args):
        return {
            "run-1": {"run_id": "run-1", "status": "success"},
            "run-2": {"run_id": "run-2", "status": "failed"},
        }

    monkeypatch.setattr(views, "Runlog", FakeRunlog)
    monkeypatch.setattr(views, "Account", FakeAccount)
    monkeypatch.setattr(views, "runlog_batch_summaries", summaries)

    data = await views._legacy_runlog_messages(
        SimpleNamespace(id="room-1", run_id=["run-1", "run-2"]),
        "tenant-1",
        0,
        50,
    )

    assert data["pagination"] == {"total": 2, "offset": 0, "limit": 50}
    assert [item["content"]["run_id"] for item in data["messages"]] == ["run-1", "run-2"]
    assert data["messages"][0]["sender_display_name"] == "系统"
