# coding=utf-8
from types import SimpleNamespace

import orjson
import pytest

from models.ticket import TICKET_STATUS_COMPLETED, TICKET_STATUS_PROCESSING
from services.ticket import websocket
from tests.ticket.test_api_list import ModelStub, ValuesModelStub, _ticket


class FakeWebSocket:
    def __init__(self, *, fail_send=False):
        self.fail_send = fail_send
        self.sent = []
        self.closed = False

    async def send(self, payload):
        if self.fail_send:
            raise RuntimeError("closed")
        self.sent.append(orjson.loads(payload))

    async def close(self):
        self.closed = True

    def __aiter__(self):
        async def messages():
            if False:
                yield None

        return messages()


@pytest.mark.asyncio
async def test_pending_snapshot_has_untruncated_count_and_excludes_terminal():
    tickets = [
        _ticket(f"ticket-{index}", status=TICKET_STATUS_PROCESSING)
        for index in range(22)
    ]
    tickets.append(_ticket("terminal", status=TICKET_STATUS_COMPLETED))
    nodes = [SimpleNamespace(ticket_id=ticket.id) for ticket in tickets]

    snapshot = await websocket.get_pending_snapshot(
        "tenant-1",
        "user-1",
        ticket_model=ModelStub(tickets),
        node_model=ValuesModelStub(nodes),
    )

    assert snapshot["type"] == "pending_update"
    assert snapshot["count"] == 22
    assert len(snapshot["items"]) == 20
    assert all(item["id"] != "terminal" for item in snapshot["items"])
    assert set(snapshot["items"][0]) == {"id", "ticket_no", "title", "priority"}


@pytest.mark.asyncio
async def test_pending_manager_isolates_tenant_and_user(monkeypatch):
    async def snapshot(tenant_id, user_id, limit=20, **kwargs):
        return {
            "type": "pending_update",
            "count": 1,
            "items": [{"id": f"{tenant_id}:{user_id}"}],
        }

    monkeypatch.setattr(websocket, "get_pending_snapshot", snapshot)
    manager = websocket.TicketPendingWebSocketManager()
    target = FakeWebSocket()
    other_tenant = FakeWebSocket()
    other_user = FakeWebSocket()
    await manager.connect("tenant-1", "user-1", "s1", target)
    await manager.connect("tenant-2", "user-1", "s2", other_tenant)
    await manager.connect("tenant-1", "user-2", "s3", other_user)

    await manager.push_users("tenant-1", ["user-1"])

    assert len(target.sent) == 2
    assert len(other_tenant.sent) == 1
    assert len(other_user.sent) == 1
    assert target.sent[-1]["items"] == [{"id": "tenant-1:user-1"}]


@pytest.mark.asyncio
async def test_pending_manager_removes_only_failed_connection(monkeypatch):
    async def snapshot(*args, **kwargs):
        return {"type": "pending_update", "count": 0, "items": []}

    monkeypatch.setattr(websocket, "get_pending_snapshot", snapshot)
    manager = websocket.TicketPendingWebSocketManager()
    healthy = FakeWebSocket()
    failed = FakeWebSocket(fail_send=True)
    await manager.connect("tenant-1", "user-1", "healthy", healthy)
    manager.active_connections["tenant-1"]["user-1"]["failed"] = failed

    await manager.push_users("tenant-1", ["user-1"])

    sessions = manager.active_connections["tenant-1"]["user-1"]
    assert set(sessions) == {"healthy"}
    assert len(healthy.sent) == 2


@pytest.mark.asyncio
async def test_after_commit_hooks_are_deferred_and_best_effort():
    calls = []
    hooks = websocket.AfterCommitHooks()

    async def success(value):
        calls.append(value)

    async def failure():
        raise RuntimeError("push failed")

    hooks.add(success, "committed")
    hooks.add(failure)
    assert calls == []

    await hooks.run()

    assert calls == ["committed"]


@pytest.mark.asyncio
async def test_publish_targets_previous_and_current_approvers(monkeypatch):
    published = []

    async def current(*args, **kwargs):
        return {"new-user"}

    class Publisher:
        async def publish(self, topic, payload):
            published.append((topic, orjson.loads(payload)))

    monkeypatch.setattr(websocket, "active_approver_ids", current)
    from libs.chat import chat_manager

    monkeypatch.setattr(chat_manager, "msg_pub", Publisher())

    await websocket.publish_ticket_pending_update(
        "tenant-1", "ticket-1", {"old-user"}
    )

    assert published[0][1] == {
        "ticket_pending": {
            "tenant_id": "tenant-1",
            "user_ids": ["new-user", "old-user"],
        }
    }


@pytest.mark.asyncio
async def test_chat_dispatch_routes_pending_event_outside_warroom(monkeypatch):
    calls = []

    async def push_users(tenant_id, user_ids):
        calls.append((tenant_id, user_ids))

    async def unexpected_broadcast(*args, **kwargs):
        raise AssertionError("ticket pending event leaked to warroom")

    from libs.chat import chat_manager

    monkeypatch.setattr(websocket.ticket_pending_manager, "push_users", push_users)
    monkeypatch.setattr(chat_manager, "_broadcast", unexpected_broadcast)
    await chat_manager._dispatch_message({
        "type": "message",
        "data": orjson.dumps({
            "ticket_pending": {
                "tenant_id": "tenant-1",
                "user_ids": ["user-1"],
            }
        }),
    })

    assert calls == [("tenant-1", ["user-1"])]


@pytest.mark.asyncio
async def test_websocket_checks_ticket_read_permission_only(monkeypatch):
    checked = []
    connected = []

    async def check_permissions(_request, *permissions, **_kwargs):
        checked.extend(permissions)

    async def connect(tenant_id, user_id, session_id, ws):
        connected.append((tenant_id, user_id, session_id))

    async def disconnect(*args):
        return None

    monkeypatch.setattr(
        "libs.security.authorization.check_permissions", check_permissions
    )
    monkeypatch.setattr(websocket.ticket_pending_manager, "connect", connect)
    monkeypatch.setattr(websocket.ticket_pending_manager, "disconnect", disconnect)
    request = SimpleNamespace(ctx=SimpleNamespace(session={
        "account_info": SimpleNamespace(id="user-1", roles=[]),
        "tenant_id": "tenant-1",
        "sub": "user-1",
        "id": "session-1",
    }))
    ws = FakeWebSocket()

    await websocket.ticket_pending_ws(request, ws)

    assert checked == ["ticket:r"]
    assert len(connected) == 1
    assert connected[0][:2] == ("tenant-1", "user-1")
    assert connected[0][2].startswith("session-1:")
    assert ws.closed is True
