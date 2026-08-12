# coding=utf-8

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.warroom import views


class _ExistsQuery:
    def __init__(self, exists):
        self._exists = exists

    async def exists(self):
        return self._exists


class _Users:
    def __init__(self, member, items=()):
        self.member = member
        self.items = list(items)
        self.added = []
        self.filters = []

    def __iter__(self):
        return iter(self.items)

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return _ExistsQuery(self.member)

    async def add(self, user):
        self.added.append(user)
        self.member = True


class _StarRooms:
    def __init__(self):
        self.added = []

    async def add(self, room):
        self.added.append(room)


def _request(user, **json_body):
    return SimpleNamespace(
        json={"id": "room-1", **json_body},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "account_info": user}),
    )


def _room_model(monkeypatch, room):
    calls = []

    class FakeRoom:
        @staticmethod
        async def get(**kwargs):
            calls.append(kwargs)
            return room

    monkeypatch.setattr(views, "Room", FakeRoom)
    return calls


def _prefetched_room_model(monkeypatch, room):
    calls = []

    class RoomQuery:
        def prefetch_related(self, *relations):
            calls.append(("prefetch_related", relations))
            return self

        def __await__(self):
            async def resolve():
                return room

            return resolve().__await__()

    class FakeRoom:
        @staticmethod
        def get(**kwargs):
            calls.append(("get", kwargs))
            return RoomQuery()

    monkeypatch.setattr(views, "Room", FakeRoom)
    return calls


def test_star_room_allows_members_without_changing_membership(monkeypatch):
    user = SimpleNamespace(id="user-1", star_rooms=_StarRooms())
    room = SimpleNamespace(id="room-1", room_type="public", users=_Users(member=True))
    room_queries = _room_model(monkeypatch, room)

    asyncio.run(inspect.unwrap(views.star_room)(_request(user)))

    assert room_queries == [{"id": "room-1", "tenant_id": "tenant-1"}]
    assert room.users.filters == [{"id": "user-1"}]
    assert room.users.added == []
    assert user.star_rooms.added == [room]


def test_star_room_rejects_non_members_without_mutating_relations(monkeypatch):
    user = SimpleNamespace(id="user-1", star_rooms=_StarRooms())
    room = SimpleNamespace(id="room-1", room_type="public", users=_Users(member=False))
    _room_model(monkeypatch, room)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(inspect.unwrap(views.star_room)(_request(user)))

    assert exc_info.value.enum is ErrorCode.ROOM_NOT_JOINED
    assert room.users.added == []
    assert user.star_rooms.added == []


def test_join_and_star_room_change_independent_relations(monkeypatch):
    user = SimpleNamespace(id="user-1", star_rooms=_StarRooms())
    room = SimpleNamespace(id="room-1", room_type="public", users=_Users(member=False))
    _room_model(monkeypatch, room)
    request = _request(user)

    async def scenario():
        await inspect.unwrap(views.join_room)(request)
        assert room.users.added == [user]
        assert user.star_rooms.added == []
        await inspect.unwrap(views.star_room)(request)

    asyncio.run(scenario())

    assert room.users.added == [user]
    assert user.star_rooms.added == [room]


def test_join_room_rejects_private_rooms_without_mutating_relations(monkeypatch):
    user = SimpleNamespace(id="user-1", star_rooms=_StarRooms())
    room = SimpleNamespace(id="room-1", room_type="private", users=_Users(member=False))
    _room_model(monkeypatch, room)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(inspect.unwrap(views.join_room)(_request(user)))

    assert exc_info.value.enum is ErrorCode.PRIVATE_ROOM_JOIN_NOT_ALLOWED
    assert room.users.filters == []
    assert room.users.added == []
    assert user.star_rooms.added == []


def test_room_not_joined_error_code_contract():
    assert ErrorCode.ROOM_NOT_JOINED.code == 4162
    assert "加入作战室" in ErrorCode.ROOM_NOT_JOINED.message
    assert ErrorCode.PRIVATE_ROOM_JOIN_NOT_ALLOWED.code == 4163
    assert "私聊作战室" in ErrorCode.PRIVATE_ROOM_JOIN_NOT_ALLOWED.message


def test_acquire_members_rejects_non_members(monkeypatch):
    user = SimpleNamespace(id="user-1")
    room = SimpleNamespace(id="room-1", users=_Users(member=False))
    room_queries = _prefetched_room_model(monkeypatch, room)

    with pytest.raises(CustomError) as exc_info:
        asyncio.run(inspect.unwrap(views.acquire_members)(_request(user)))

    assert exc_info.value.enum is ErrorCode.ROOM_MEMBER_ACCESS_DENIED
    assert room.users.filters == [{"id": "user-1"}]
    assert room_queries == [
        ("get", {"id": "room-1", "tenant_id": "tenant-1"}),
        ("prefetch_related", ("creator", "users")),
    ]


def test_acquire_members_returns_tenant_members_to_room_members(monkeypatch):
    requester = SimpleNamespace(id="user-1")

    async def tenant_members():
        return [SimpleNamespace(id="tenant-1")]

    async def roles():
        return [SimpleNamespace(name="operator")]

    member = SimpleNamespace(
        id="user-2",
        username="member",
        display_name="成员",
        member=SimpleNamespace(all=tenant_members),
        roles=SimpleNamespace(all=roles),
    )
    room = SimpleNamespace(
        id="room-1",
        users=_Users(member=True, items=[member]),
    )
    _prefetched_room_model(monkeypatch, room)

    response = asyncio.run(inspect.unwrap(views.acquire_members)(_request(requester)))

    assert orjson.loads(response.body) == {
        "code": 0,
        "data": {
            "members": [
                {
                    "id": "user-2",
                    "username": "member",
                    "display_name": "成员",
                    "roles": ["operator"],
                }
            ]
        },
    }


def test_get_all_rooms_filters_private_rooms_and_deduplicates(monkeypatch):
    user = SimpleNamespace(id="user-1")
    calls = []
    rooms = [SimpleNamespace(json={"id": "public-room"})]

    class FakeQuery:
        def distinct(self):
            calls.append(("distinct",))
            return self

        def offset(self, value):
            calls.append(("offset", value))
            return self

        def limit(self, value):
            calls.append(("limit", value))
            return self

        def order_by(self, *values):
            calls.append(("order_by", values))
            return self

        def prefetch_related(self, *values):
            calls.append(("prefetch_related", values))
            return self

        def __await__(self):
            async def resolve():
                return rooms

            return resolve().__await__()

        async def count(self):
            calls.append(("count",))
            return 1

    captured = {}

    class FakeRoom:
        @staticmethod
        def filter(condition):
            captured["condition"] = condition
            return FakeQuery()

    monkeypatch.setattr(views, "Room", FakeRoom)

    asyncio.run(
        inspect.unwrap(views.get_all_rooms)(
            _request(user, page=2, size=5, keyword="响应")
        )
    )

    condition = captured["condition"]
    assert _matches_room(
        condition,
        tenant_id="tenant-1",
        room_type="public",
        member_ids=set(),
        name="应急响应",
    )
    assert _matches_room(
        condition,
        tenant_id="tenant-1",
        room_type="private",
        member_ids={"user-1", "user-2"},
        name="响应私聊",
    )
    assert not _matches_room(
        condition,
        tenant_id="tenant-1",
        room_type="private",
        member_ids={"user-2", "user-3"},
        name="响应私聊",
    )
    assert not _matches_room(
        condition,
        tenant_id="tenant-2",
        room_type="public",
        member_ids=set(),
        name="应急响应",
    )
    assert calls == [
        ("distinct",),
        ("offset", 5),
        ("limit", 5),
        ("order_by", ("-ctime",)),
        ("prefetch_related", ("creator", "users")),
        ("count",),
    ]


def test_join_and_star_room_permission_contracts():
    tree = ast.parse(Path(views.__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }

    assert _required_permission(functions["join_room"]) == "warroom:u"
    assert _required_permission(functions["star_room"]) == "warroom:r"


def _required_permission(function):
    for decorator in function.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "requires_permission"
        ):
            return ast.literal_eval(decorator.args[0])
    raise AssertionError(f"{function.name} 缺少 requires_permission 装饰器")


def _matches_room(condition, *, tenant_id, room_type, member_ids, name):
    values = {
        "tenant_id": tenant_id,
        "room_type": room_type,
        "users__id": member_ids,
        "name__icontains": name,
    }

    if condition.filters:
        matches = True
        for key, expected in condition.filters.items():
            actual = values[key]
            if key == "users__id":
                matches = matches and expected in actual
            elif key == "name__icontains":
                matches = matches and expected.casefold() in actual.casefold()
            else:
                matches = matches and actual == expected
    else:
        child_matches = [
            _matches_room(
                child,
                tenant_id=tenant_id,
                room_type=room_type,
                member_ids=member_ids,
                name=name,
            )
            for child in condition.children
        ]
        matches = (
            any(child_matches)
            if condition.join_type == condition.OR
            else all(child_matches)
        )

    return not matches if condition._is_negated else matches
