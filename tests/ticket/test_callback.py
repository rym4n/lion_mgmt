# coding=utf-8
"""External ticket callback token validation and one-shot consumption."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import orjson
import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.ticket import views
from tests.ticket.conftest import make_ticket


def test_callback_route_supports_get_and_post():
    routes = [
        route for route in views.ticket_bp._future_routes
        if route.uri == "/callback"
    ]
    assert len(routes) == 1
    assert routes[0].methods == frozenset({"GET", "POST"})


class CallbackQuery:
    def __init__(self, model, filters):
        self.model = model
        self.filters = filters
        self.connection = None
        self.locked = False

    def using_db(self, connection):
        self.connection = connection
        return self

    def select_for_update(self):
        self.locked = True
        return self

    async def first(self):
        ticket = self.model.ticket
        if (
            ticket is not None
            and ticket.callback_token == self.filters.get("callback_token")
            and ticket.deleted is self.filters.get("deleted")
        ):
            return ticket
        return None


class TicketModel:
    def __init__(self, ticket):
        self.ticket = ticket
        self.queries = []

    def filter(self, **filters):
        query = CallbackQuery(self, filters)
        self.queries.append(query)
        return query


def _request(method, token=None, **binding):
    args = {}
    body = {}
    target = args if method == "GET" else body
    if token is not None:
        target["token"] = token
    target.update(binding)
    return SimpleNamespace(
        method=method,
        args=args,
        json=body,
        remote_addr="198.51.100.7",
    )


def _install(monkeypatch, ticket):
    model = TicketModel(ticket)
    activities = []
    advances = []
    connection = object()

    @asynccontextmanager
    async def transaction():
        yield connection

    async def create_activity(**kwargs):
        activities.append(kwargs)

    async def advance(ticket_arg, actor_id=None, executor=None):
        advances.append((ticket_arg, actor_id, executor))
        return True

    monkeypatch.setattr(views, "Ticket", model)
    monkeypatch.setattr(views, "in_transaction", transaction)
    monkeypatch.setattr(views.TicketActivity, "create", create_activity)
    monkeypatch.setattr(views, "advance_ticket", advance)
    return model, activities, advances, connection


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_callback_accepts_query_or_body_token(monkeypatch, method):
    ticket = make_ticket()
    ticket.callback_token = "valid-token"
    model, activities, advances, connection = _install(monkeypatch, ticket)

    response = await views.callback_view(_request(method, "valid-token"))

    assert orjson.loads(response.body) == {"code": 0, "data": None}
    assert ticket.callback_token is None
    assert ticket.saved[-1] == ("callback_token", "utime")
    assert ticket.saved_using_db[-1] is connection
    assert model.queries[0].locked is True
    assert advances == [(ticket, None, "外部回调")]
    assert activities[0]["payload"] == {
        "executor": "外部回调",
        "actor": {"username": "callback", "display_name": "外部回调"},
        "remote_addr": "198.51.100.7",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "wrong-token"])
async def test_callback_rejects_missing_or_invalid_token(monkeypatch, token):
    ticket = make_ticket()
    ticket.callback_token = "valid-token"
    _model, activities, advances, _connection = _install(monkeypatch, ticket)

    with pytest.raises(CustomError) as exc:
        await views.callback_view(_request("GET", token))

    assert exc.value.enum is ErrorCode.TICKET_CALLBACK_TOKEN_INVALID
    assert ticket.callback_token == "valid-token"
    assert activities == []
    assert advances == []


@pytest.mark.asyncio
async def test_callback_token_is_one_time(monkeypatch):
    ticket = make_ticket()
    ticket.callback_token = "one-shot"
    _model, _activities, advances, _connection = _install(monkeypatch, ticket)
    request = _request("POST", "one-shot")

    await views.callback_view(request)
    with pytest.raises(CustomError) as exc:
        await views.callback_view(request)

    assert exc.value.enum is ErrorCode.TICKET_CALLBACK_TOKEN_INVALID
    assert len(advances) == 1


@pytest.mark.asyncio
async def test_callback_rejects_mismatched_ticket_or_tenant(monkeypatch):
    ticket = make_ticket(ticket_id="ticket-1", tenant_id="tenant-1")
    ticket.callback_token = "bound-token"
    _model, activities, advances, _connection = _install(monkeypatch, ticket)

    with pytest.raises(CustomError) as exc:
        await views.callback_view(
            _request(
                "POST",
                "bound-token",
                ticket_id="ticket-1",
                tenant_id="tenant-2",
            )
        )

    assert exc.value.enum is ErrorCode.TICKET_CALLBACK_TOKEN_INVALID
    assert ticket.callback_token == "bound-token"
    assert activities == []
    assert advances == []
