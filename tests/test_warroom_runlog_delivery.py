# coding=utf-8
import orjson
import pytest

from libs.chat import ChatManager


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

    async def create_message(**kwargs):
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
                        "tenant_id": "tenant-1",
                    },
                }
            ),
        }
    )

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
                        "tenant_id": "tenant-1",
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
