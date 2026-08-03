# coding=utf-8
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.event import views as event_views


ROOT = Path(__file__).resolve().parents[1]
VIEWS_PATH = ROOT / "services/event/views.py"


def _function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(VIEWS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _is_initialize_room_guard(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "initialize_room"


def _calls_attr(node: ast.AST, attr: str) -> bool:
    return any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == attr for child in ast.walk(node))


class _AwaitableQuery:
    def __init__(self, value):
        self.value = value

    def prefetch_related(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def all(self):
        return self

    def __await__(self):
        async def resolve():
            return self.value

        return resolve().__await__()


def test_event_detail_defaults_to_read_only():
    node = _function("get_event_detail")

    guarded_calls = []
    unguarded_calls = []
    for statement in node.body:
        if isinstance(statement, ast.If) and _is_initialize_room_guard(statement.test):
            guarded_calls.extend(statement.body)
            continue
        if _calls_attr(statement, "save") or _calls_attr(statement, "add"):
            unguarded_calls.append(statement)

    assert unguarded_calls == []
    assert any(_calls_attr(statement, "save") for statement in guarded_calls)
    assert sum(1 for statement in guarded_calls if _calls_attr(statement, "add")) == 2


def test_event_detail_requires_explicit_room_initialization_flag():
    node = _function("get_event_detail")

    assignments = [statement for statement in node.body if isinstance(statement, ast.Assign)]
    initialize_room_assignment = next(
        statement
        for statement in assignments
        if any(isinstance(target, ast.Name) and target.id == "initialize_room" for target in statement.targets)
    )

    assert isinstance(initialize_room_assignment.value, ast.Call)
    assert isinstance(initialize_room_assignment.value.func, ast.Name)
    assert initialize_room_assignment.value.func.id == "bool"
    assert ast.unparse(initialize_room_assignment.value.args[0]) == "request.json.get('initialize_room')"


def test_event_detail_checks_warroom_permission_before_initialization():
    node = _function("get_event_detail")

    initialization_guard = next(
        statement
        for statement in node.body
        if isinstance(statement, ast.If) and _is_initialize_room_guard(statement.test)
    )

    assert ast.unparse(initialization_guard.body[0]) == "await check_permissions(request, 'warroom:r')"


def test_event_detail_updates_concrete_room_foreign_key_field():
    node = _function("get_event_detail")

    initialization_guard = next(
        statement
        for statement in node.body
        if isinstance(statement, ast.If) and _is_initialize_room_guard(statement.test)
    )
    missing_room_guard = next(
        statement
        for statement in ast.walk(initialization_guard)
        if isinstance(statement, ast.If) and ast.unparse(statement.test) == "not event.room"
    )
    appended_fields = [
        child.args[0].value
        for child in ast.walk(missing_room_guard)
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "append"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "update_fields"
            and len(child.args) == 1
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        )
    ]

    assert appended_fields == ["room_id"]


def test_event_detail_uses_persisted_creator():
    node = _function("get_event_detail")

    event_dict_assignment = next(
        statement
        for statement in node.body
        if (
            isinstance(statement, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "event_dict" for target in statement.targets)
        )
    )

    assert ast.unparse(event_dict_assignment.value) == "{'created_name': event.creator.username if event.creator else ''}"


def test_event_detail_initializes_room_for_manual_event(monkeypatch):
    viewer = SimpleNamespace(
        id="account-1",
        username="admin",
        display_name="Administrator",
        star_rooms=SimpleNamespace(add=AsyncMock()),
    )
    event_creator = SimpleNamespace(
        id="account-2",
        username="source-owner",
        display_name="Source Owner",
    )
    room = SimpleNamespace(
        id="room-1",
        users=SimpleNamespace(add=AsyncMock(), all=AsyncMock(return_value=[viewer])),
    )

    class FakeEvent:
        id = "event-1"
        event_name = "manual-event"
        event_process_status = "待处理"
        owner = {}
        room = None
        creator = event_creator
        save = AsyncMock()

        @property
        def json(self):
            return {
                "id": self.id,
                "event_name": self.event_name,
                "event_process_status": self.event_process_status,
                "room": self.room.id if self.room else None,
            }

    event = FakeEvent()

    class FakeEvents:
        @staticmethod
        def get(**_filters):
            return _AwaitableQuery(event)

    class FakeRoom:
        create = AsyncMock(return_value=room)

        @staticmethod
        def filter(**_filters):
            return SimpleNamespace(exists=AsyncMock(return_value=False))

    class FakeRunlog:
        @staticmethod
        def filter(**_filters):
            return _AwaitableQuery([])

    check_permissions = AsyncMock()
    monkeypatch.setattr(event_views, "Events", FakeEvents)
    monkeypatch.setattr(event_views, "Room", FakeRoom)
    monkeypatch.setattr(event_views, "Runlog", FakeRunlog)
    monkeypatch.setattr(event_views, "check_permissions", check_permissions)
    monkeypatch.setattr(event_views, "runlog_list", AsyncMock(return_value=[]))
    monkeypatch.setattr(event_views, "gen_random_code", lambda _length: "ABC123")
    monkeypatch.setattr(event_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={"id": event.id, "initialize_room": True},
        ctx=SimpleNamespace(session={"tenant_id": "tenant-1", "account_info": viewer}),
    )
    handler = event_views.get_event_detail
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__

    result = asyncio.run(handler(request))

    check_permissions.assert_awaited_once_with(request, "warroom:r")
    FakeRoom.create.assert_awaited_once_with(
        name="manual-event_ABC123",
        creator=viewer,
        tenant_id="tenant-1",
        room_type="public",
    )
    event.save.assert_awaited_once_with(update_fields=["room_id", "event_process_status"])
    viewer.star_rooms.add.assert_awaited_once_with(room)
    room.users.add.assert_awaited_once_with(viewer)
    assert result["created_name"] == event_creator.username
    assert result["room"] == room.id
    assert result["event_process_status"] == "处理中"
