# coding=utf-8

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("RY_REDIS_HOST", "127.0.0.1")
os.environ.setdefault("RY_REDIS_PORT", "6379")
os.environ.setdefault("RY_REDIS_DB", "0")
os.environ.setdefault("RY_CHAT_DB", "1")

from services.warroom.views import _dedupe_star_rooms, ensure_default_ai_private_room
from libs import chat as chat_module


class _StarRooms:
    def __init__(self):
        self.added = []

    async def add(self, room):
        self.added.append(room)


def test_ensure_default_ai_private_room_stars_system_private_room(monkeypatch):
    user = SimpleNamespace(id="user-1", star_rooms=_StarRooms())
    system_user = SimpleNamespace(id="system-1", star_rooms=_StarRooms())
    room = SimpleNamespace(id="room-1")
    calls = {}

    class FakeAccount:
        @staticmethod
        async def get_or_none(**kwargs):
            calls["account_query"] = kwargs
            return system_user

    class FakeRoom:
        @staticmethod
        async def get_or_none(**kwargs):
            calls["room_query"] = kwargs
            return room

    async def fake_get_or_create(cache_client, sender, receiver, tenant_id):
        calls["get_or_create"] = (cache_client, sender, receiver, tenant_id)
        return {"room_id": room.id}

    monkeypatch.setattr("services.warroom.views.Account", FakeAccount)
    monkeypatch.setattr("services.warroom.views.Room", FakeRoom)
    monkeypatch.setattr(
        "services.warroom.views.get_or_create_private_chat_room", fake_get_or_create
    )

    asyncio.run(ensure_default_ai_private_room("cache", user, "tenant-1"))

    assert calls["account_query"] == {"username": "system", "disabled": False}
    assert calls["get_or_create"] == ("cache", user, system_user, "tenant-1")
    assert calls["room_query"] == {"id": room.id, "tenant_id": "tenant-1"}
    assert user.star_rooms.added == [room]
    assert system_user.star_rooms.added == [room]


def test_dedupe_star_rooms_keeps_one_private_room_per_participant_set():
    user = SimpleNamespace(id="user-1", username="admin")
    system = SimpleNamespace(id="system-1", username="system")
    other = SimpleNamespace(id="user-2", username="alice")
    duplicate_ai_old = SimpleNamespace(
        id="room-old",
        room_type="private",
        users=[user, system],
        json={"id": "room-old"},
    )
    duplicate_ai_new = SimpleNamespace(
        id="room-new",
        room_type="private",
        users=[system, user],
        json={"id": "room-new"},
    )
    other_private = SimpleNamespace(
        id="room-other",
        room_type="private",
        users=[user, other],
        json={"id": "room-other"},
    )
    public_room = SimpleNamespace(
        id="room-public",
        room_type="public",
        users=[user, system],
        json={"id": "room-public"},
    )

    rooms = _dedupe_star_rooms(
        [duplicate_ai_new, public_room, duplicate_ai_old, other_private]
    )

    assert [room.id for room in rooms] == [
        "room-new",
        "room-public",
        "room-other",
    ]


def test_warroom_ai_stream_delegates_user_rbac_and_tenant_identity(monkeypatch):
    captured = {}
    published = []

    async def fake_stream_prompt_events(**kwargs):
        captured.update(kwargs)
        yield "delta", {"content": "ok"}

    async def fake_resolve_llm(tenant_id):
        assert tenant_id == "tenant-1"
        return SimpleNamespace(model_name="aioe")

    async def fake_publish_event(*_args, **_kwargs):
        return True

    async def fake_publish(topic, payload):
        published.append((topic, payload))

    delegated = {
        "base_url": "http://lion-mgmt:8000",
        "token": "account-1:signed-token",
        "tenant_id": "tenant-1",
    }
    user = SimpleNamespace(id="account-1")
    manager = chat_module.ChatManager.__new__(chat_module.ChatManager)
    manager.ai_task_locks = {}
    manager.msg_pub = SimpleNamespace(publish=fake_publish)
    manager._publish_ai_stream_event = fake_publish_event

    monkeypatch.setattr(chat_module, "stream_prompt_events", fake_stream_prompt_events)
    monkeypatch.setattr(
        chat_module,
        "build_warroom_aioe_credentials",
        lambda account, tenant_id: delegated,
    )
    monkeypatch.setattr("services.llm_config.resolver.resolve_llm", fake_resolve_llm)

    asyncio.run(
        manager._execute_lion_agent_task(
            user=user,
            tenant_id="tenant-1",
            current_room_id="room-1",
            content="inspect router CPU",
            message_data={"message_id": "message-1"},
            stream_id="stream-1",
        )
    )

    assert captured["workspace_id"] == "tenant-1:warroom"
    assert captured["memory"] == {
        "enabled": False,
        "tenant_id": "tenant-1",
        "employee_id": None,
    }
    assert captured["aioe"] == delegated
    assert captured["request_id"] == "stream-1"
    assert len(published) == 1
