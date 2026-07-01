# coding=utf-8

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("RY_REDIS_HOST", "127.0.0.1")
os.environ.setdefault("RY_REDIS_PORT", "6379")
os.environ.setdefault("RY_REDIS_DB", "0")
os.environ.setdefault("RY_CHAT_DB", "1")

from services.warroom.views import ensure_default_ai_private_room


class _StarRooms:
    def __init__(self):
        self.added = []

    async def add(self, room):
        self.added.append(room)


@pytest.mark.asyncio
async def test_ensure_default_ai_private_room_stars_system_private_room(monkeypatch):
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
    monkeypatch.setattr("services.warroom.views.get_or_create_private_chat_room", fake_get_or_create)

    await ensure_default_ai_private_room("cache", user, "tenant-1")

    assert calls["account_query"] == {"username": "system", "disabled": False}
    assert calls["get_or_create"] == ("cache", user, system_user, "tenant-1")
    assert calls["room_query"] == {"id": room.id, "tenant_id": "tenant-1"}
    assert user.star_rooms.added == [room]
    assert system_user.star_rooms.added == [room]
