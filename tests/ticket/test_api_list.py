# coding=utf-8
"""工单四 tab 列表与通知中心待办接口测试。"""

from datetime import datetime, timedelta
from inspect import unwrap
from types import SimpleNamespace

import orjson
import pytest
from tortoise.expressions import Q

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.ticket import (
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    TICKET_STATUS_COMPLETED,
    TICKET_STATUS_PROCESSING,
    TICKET_STATUS_REJECTED,
    TICKET_TERMINAL_STATUSES,
)
from services.ticket import views
from tests.ticket.conftest import FakeQuery, make_ticket


def _ticket(ticket_id, *, tenant_id="tenant-1", creator_id="creator", status=0,
            title=None, ticket_no=None, ctime=None, sla_resolve_at=None,
            closed_at=None):
    ticket = make_ticket(
        ticket_id=ticket_id,
        tenant_id=tenant_id,
        status=status,
        ctime=ctime,
        sla_resolve_at=sla_resolve_at,
    )
    ticket.creator_id = creator_id
    ticket.title = title or f"工单 {ticket_id}"
    ticket.ticket_no = ticket_no or f"TKT-{ticket_id}"
    ticket.closed_at = closed_at
    ticket.json = {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "status": ticket.status,
        "creator_id": ticket.creator_id,
        "tenant_id": ticket.tenant_id,
        "sla_resolve_at": ticket.sla_resolve_at,
        "closed_at": ticket.closed_at,
    }
    return ticket


class Query(FakeQuery):
    """支持列表接口所需过滤/排序/分页，并记录所有 ORM 调用。"""

    def __init__(self, rows=None, calls=None):
        super().__init__(rows)
        self.calls = calls if calls is not None else []
        self._offset = 0
        self._limit = None

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        rows = self.rows
        for key, value in kwargs.items():
            if key.endswith("__in"):
                field = key[:-4]
                rows = [row for row in rows if getattr(row, field) in value]
            elif key.endswith("__icontains"):
                field = key[:-11]
                rows = [
                    row for row in rows
                    if str(value).lower() in str(getattr(row, field)).lower()
                ]
            elif key.endswith("__gte"):
                field = key[:-5]
                rows = [row for row in rows if getattr(row, field) >= value]
            elif key.endswith("__lte"):
                field = key[:-5]
                rows = [row for row in rows if getattr(row, field) <= value]
            else:
                rows = [row for row in rows if getattr(row, key) == value]
        for expression in args:
            assert isinstance(expression, Q)
            rows = [row for row in rows if self._matches_q(row, expression)]
        self.rows = rows
        return self

    @classmethod
    def _matches_q(cls, row, expression):
        matches = []
        for child in expression.children:
            matches.append(cls._matches_q(row, child))
        for key, value in expression.filters.items():
            if key.endswith("__icontains"):
                field = key[:-11]
                matches.append(str(value).lower() in str(getattr(row, field)).lower())
            else:
                matches.append(getattr(row, key) == value)
        if not matches:
            return True
        return any(matches) if expression.join_type == Q.OR else all(matches)

    def order_by(self, *fields):
        self.calls.append(("order_by", fields, {}))
        for field in reversed(fields):
            descending = field.startswith("-")
            name = field.lstrip("-")
            self.rows.sort(
                key=lambda row: (getattr(row, name) is None, getattr(row, name)),
                reverse=descending,
            )
        return self

    def offset(self, value):
        self.calls.append(("offset", (value,), {}))
        self._offset = value
        return self

    def limit(self, value):
        self.calls.append(("limit", (value,), {}))
        self._limit = value
        return self

    async def all(self):
        end = None if self._limit is None else self._offset + self._limit
        return self.rows[self._offset:end]


class ValuesQuery(FakeQuery):
    def __init__(self, rows=None, calls=None, *, error=None):
        super().__init__(rows)
        self.calls = calls if calls is not None else []
        self.error = error

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        return self

    async def values_list(self, *fields, flat=False):
        if self.error:
            raise self.error
        if flat:
            assert len(fields) == 1
            return [getattr(row, fields[0]) for row in self.rows]
        return [tuple(getattr(row, field) for field in fields) for row in self.rows]


class ModelStub:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def filter(self, *args, **kwargs):
        return Query(self.rows, self.calls).filter(*args, **kwargs)


class ValuesModelStub:
    def __init__(self, rows=(), *, error=None):
        self.rows = list(rows)
        self.error = error
        self.calls = []

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        return ValuesQuery(self.rows, self.calls, error=self.error)


class ExplodingModel:
    def __init__(self, error):
        self.error = error
        self.calls = []

    def filter(self, *args, **kwargs):
        self.calls.append(("filter", args, kwargs))
        raise self.error


def _request(
    args=None,
    *,
    user_id="user-1",
    tenant_id="tenant-1",
    permissions=(),
):
    roles = []
    if permissions:
        roles.append(
            SimpleNamespace(
                id="tenant-role",
                tenant_id=tenant_id,
                permissions=list(permissions),
            )
        )
    return SimpleNamespace(
        args=args or {},
        ctx=SimpleNamespace(session={
            "sub": user_id,
            "tenant_id": tenant_id,
            "account_info": SimpleNamespace(roles=roles),
        }),
    )


def _payload(response):
    return orjson.loads(response.body)


async def _list(request):
    return await unwrap(views.list_view)(request)


async def _detail(request, ticket_id):
    return await unwrap(views.detail_view)(request, ticket_id)


async def _todolist(request):
    return await unwrap(views.todolist_view)(request)


def _filter_kwargs(calls):
    return [kwargs for name, _args, kwargs in calls if name == "filter"]


@pytest.fixture(autouse=True)
def _empty_batch_relations(monkeypatch):
    for name in ("TicketType", "TicketFlowVersion", "TicketFlow", "Account"):
        monkeypatch.setattr(views, name, ValuesModelStub(), raising=False)


@pytest.mark.asyncio
async def test_my_todo_returns_only_active_approver_tickets_sorted_by_sla(monkeypatch):
    now = datetime(2026, 8, 11, 9, 0, 0)
    late = _ticket("late", sla_resolve_at=now + timedelta(hours=2))
    urgent = _ticket("urgent", sla_resolve_at=now + timedelta(minutes=10))
    other = _ticket("other", sla_resolve_at=now + timedelta(minutes=1))
    nodes = [
        SimpleNamespace(ticket_id="late"),
        SimpleNamespace(ticket_id="urgent"),
    ]
    ticket_model = ModelStub([late, urgent, other])
    node_model = ValuesModelStub(nodes)
    monkeypatch.setattr(views, "Ticket", ticket_model)
    monkeypatch.setattr(views, "TicketNodeInstance", node_model)

    body = _payload(await _list(_request({"view": "my_todo"})))

    assert [item["id"] for item in body["data"]["data"]] == ["urgent", "late"]
    assert {
        "tenant_id": "tenant-1",
        "deleted": False,
        "status__in": [NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
        "approvers__id": "user-1",
    } in _filter_kwargs(node_model.calls)
    assert {"tenant_id": "tenant-1", "deleted": False} in _filter_kwargs(ticket_model.calls)
    assert len([call for call in node_model.calls if call[0] == "filter"]) == 1
    assert len([call for call in ticket_model.calls if call[0] == "filter"]) == 2


@pytest.mark.asyncio
async def test_my_apply_returns_created_tickets_with_status_filter(monkeypatch):
    mine_open = _ticket("mine-open", creator_id="user-1", status=TICKET_STATUS_PROCESSING)
    mine_done = _ticket("mine-done", creator_id="user-1", status=TICKET_STATUS_COMPLETED)
    others = _ticket("others", creator_id="user-2", status=TICKET_STATUS_COMPLETED)
    ticket_model = ModelStub([mine_open, mine_done, others])
    monkeypatch.setattr(views, "Ticket", ticket_model)

    body = _payload(await _list(_request({
        "view": "my_apply",
        "status": str(TICKET_STATUS_PROCESSING),
    })))

    assert [item["id"] for item in body["data"]["data"]] == ["mine-open"]
    filters = _filter_kwargs(ticket_model.calls)
    assert {"creator_id": "user-1"} in filters
    assert {"status": TICKET_STATUS_PROCESSING} in filters


@pytest.mark.asyncio
async def test_my_apply_without_status_includes_processing_and_terminal(monkeypatch):
    tickets = [
        _ticket("open", creator_id="user-1", status=TICKET_STATUS_PROCESSING),
        _ticket("done", creator_id="user-1", status=TICKET_STATUS_COMPLETED),
        _ticket("other", creator_id="user-2", status=TICKET_STATUS_COMPLETED),
    ]
    monkeypatch.setattr(views, "Ticket", ModelStub(tickets))

    body = _payload(await _list(_request({"view": "my_apply"})))

    assert {item["id"] for item in body["data"]["data"]} == {"open", "done"}


@pytest.mark.asyncio
async def test_my_history_returns_terminal_participated_tickets_with_creator_badge(monkeypatch):
    now = datetime(2026, 8, 11, 9, 0, 0)
    created = _ticket(
        "created", creator_id="user-1", status=TICKET_STATUS_COMPLETED,
        closed_at=now - timedelta(minutes=5),
    )
    participated = _ticket(
        "participated", creator_id="other", status=TICKET_STATUS_REJECTED,
        closed_at=now,
    )
    processing = _ticket("processing", creator_id="other", status=TICKET_STATUS_PROCESSING)
    activities = [
        SimpleNamespace(ticket_id="created"),
        SimpleNamespace(ticket_id="participated"),
        SimpleNamespace(ticket_id="processing"),
    ]
    ticket_model = ModelStub([created, participated, processing])
    activity_model = ValuesModelStub(activities)
    monkeypatch.setattr(views, "Ticket", ticket_model)
    monkeypatch.setattr(views, "TicketActivity", activity_model)

    body = _payload(await _list(_request({"view": "my_history"})))

    assert [(item["id"], item["is_creator"]) for item in body["data"]["data"]] == [
        ("participated", False),
        ("created", True),
    ]
    assert {"tenant_id": "tenant-1", "deleted": False, "actor_id": "user-1"} in [
        {key: value for key, value in kwargs.items() if key != "action__in"}
        for kwargs in _filter_kwargs(activity_model.calls)
    ]
    assert any(
        kwargs.get("status__in") == list(TICKET_TERMINAL_STATUSES)
        for kwargs in _filter_kwargs(ticket_model.calls)
    )


@pytest.mark.asyncio
async def test_all_requires_ticket_all_permission(monkeypatch):
    monkeypatch.setattr(views, "Ticket", ModelStub([]))
    monkeypatch.setattr(views, "is_ticket_admin", lambda _session: False)

    with pytest.raises(CustomError) as exc:
        await _list(_request({"view": "all"}))

    assert exc.value.enum is ErrorCode.TICKET_PERM_DENIED


@pytest.mark.asyncio
async def test_all_returns_tenant_rows_when_authorized(monkeypatch):
    own = _ticket("own")
    foreign = _ticket("foreign", tenant_id="tenant-2")
    deleted = _ticket("deleted")
    deleted.deleted = True
    ticket_model = ModelStub([own, foreign, deleted])
    monkeypatch.setattr(views, "Ticket", ticket_model)

    body = _payload(
        await _list(_request({"view": "all"}, permissions=("ticket:all",)))
    )

    assert [item["id"] for item in body["data"]["data"]] == ["own"]
    assert {"tenant_id": "tenant-1", "deleted": False} in _filter_kwargs(ticket_model.calls)


@pytest.mark.asyncio
async def test_link_search_is_rejected_after_link_feature_removal(monkeypatch):
    monkeypatch.setattr(views, "Ticket", ModelStub([]))

    with pytest.raises(CustomError) as exc:
        await _list(_request({"view": "link_search"}, permissions=("ticket:u",)))

    assert exc.value.enum is ErrorCode.PARAM_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{}, {"view": "unknown"}])
async def test_list_rejects_missing_or_invalid_view(monkeypatch, args):
    monkeypatch.setattr(views, "Ticket", ModelStub([]))

    with pytest.raises(CustomError) as exc:
        await _list(_request(args))

    assert exc.value.enum is ErrorCode.PARAM_ERROR


@pytest.mark.asyncio
async def test_list_supports_keyword_and_pagination(monkeypatch):
    now = datetime(2026, 8, 11, 9, 0, 0)
    tickets = [
        _ticket(
            "1", creator_id="user-1", title="数据库扩容", ticket_no="CHG-001",
            ctime=now,
        ),
        _ticket(
            "2", creator_id="user-1", title="数据库巡检", ticket_no="CHG-002",
            ctime=now + timedelta(minutes=1),
        ),
        _ticket(
            "3", creator_id="user-1", title="网络变更", ticket_no="NET-003",
            ctime=now + timedelta(minutes=2),
        ),
    ]
    ticket_model = ModelStub(tickets)
    monkeypatch.setattr(views, "Ticket", ticket_model)

    body = _payload(await _list(_request({
        "view": "my_apply", "keyword": "数据库", "page": "2", "size": "1",
    })))

    assert body["data"]["total"] == 2
    assert body["data"]["page"] == 2
    assert body["data"]["size"] == 1
    assert body["data"]["total_pages"] == 2
    assert [item["id"] for item in body["data"]["data"]] == ["1"]
    assert any(args and isinstance(args[0], Q) for _name, args, _kwargs in ticket_model.calls)


@pytest.mark.asyncio
async def test_list_applies_supported_column_filters_and_ignores_priority(monkeypatch):
    now = datetime(2026, 8, 11, 9, 0, 0)
    matched = _ticket(
        "matched",
        creator_id="user-1",
        title="数据库扩容",
        ticket_no="CHG-001",
        ctime=now,
    )
    matched.ticket_type_id = "type-1"
    ignored = _ticket(
        "ignored",
        creator_id="user-1",
        title="网络巡检",
        ticket_no="NET-002",
        ctime=now - timedelta(days=2),
    )
    ignored.ticket_type_id = "type-2"
    ticket_model = ModelStub([matched, ignored])
    monkeypatch.setattr(views, "Ticket", ticket_model)

    body = _payload(await _list(_request({
        "view": "my_apply",
        "ticket_no": "CHG",
        "title": "扩容",
        "ticket_type_id": "type-1",
        "priority": "2",
        "creator_id": "user-1",
        "ctime_start": (now - timedelta(hours=1)).isoformat(),
        "ctime_end": (now + timedelta(hours=1)).isoformat(),
    })))

    assert [item["id"] for item in body["data"]["data"]] == ["matched"]
    assert body["data"]["data"][0]["ticket_type_id"] == "type-1"
    assert body["data"]["data"][0]["creator_id"] == "user-1"
    filters = _filter_kwargs(ticket_model.calls)
    assert {"ticket_no__icontains": "CHG"} in filters
    assert {"title__icontains": "扩容"} in filters
    assert {"ticket_type_id": "type-1"} in filters
    assert {"priority": 2} not in filters


@pytest.mark.asyncio
async def test_list_caps_page_size_at_one_hundred(monkeypatch):
    ticket_model = ModelStub([])
    monkeypatch.setattr(views, "Ticket", ticket_model)

    body = _payload(await _list(_request({"view": "my_apply", "size": "500"})))

    assert body["data"]["size"] == 100
    assert any(name == "limit" and args == (100,) for name, args, _kwargs in ticket_model.calls)


@pytest.mark.asyncio
async def test_list_loads_related_names_in_batches_with_tenant_filters(monkeypatch):
    ticket = _ticket("related", creator_id="user-1")
    type_model = ValuesModelStub([SimpleNamespace(id="type-1", name="变更")])
    version_model = ValuesModelStub([
        SimpleNamespace(id="ver-1", ticket_flow_id="flow-1")
    ])
    flow_model = ValuesModelStub([SimpleNamespace(id="flow-1", name="标准变更流程")])
    account_model = ValuesModelStub([
        SimpleNamespace(id="user-1", display_name="申请人")
    ])
    monkeypatch.setattr(views, "Ticket", ModelStub([ticket]))
    monkeypatch.setattr(views, "TicketType", type_model)
    monkeypatch.setattr(views, "TicketFlowVersion", version_model)
    monkeypatch.setattr(views, "TicketFlow", flow_model)
    monkeypatch.setattr(views, "Account", account_model)

    body = _payload(await _list(_request({"view": "my_apply"})))

    item = body["data"]["data"][0]
    assert item["ticket_type_name"] == "变更"
    assert item["flow_name"] == "标准变更流程"
    assert item["creator_name"] == "申请人"
    for model in (type_model, version_model, flow_model, account_model):
        assert len(_filter_kwargs(model.calls)) == 1
        assert _filter_kwargs(model.calls)[0]["tenant_id"] == "tenant-1"
        assert _filter_kwargs(model.calls)[0]["deleted"] is False


@pytest.mark.asyncio
async def test_detail_returns_related_type_and_creator_names(monkeypatch):
    ticket = _ticket("detail", creator_id="user-1")
    ticket.ticket_type_id = "type-1"
    ticket.flow_version_id = "ver-1"
    ticket.json["ticket_type_id"] = ticket.ticket_type_id
    ticket.json["flow_version_id"] = ticket.flow_version_id
    ticket_model = ModelStub([ticket])
    node_model = ModelStub([])
    activity_model = ModelStub([])
    monkeypatch.setattr(views, "Ticket", ticket_model)
    monkeypatch.setattr(views, "TicketNodeInstance", node_model)
    monkeypatch.setattr(views, "TicketActivity", activity_model)
    monkeypatch.setattr(
        views,
        "TicketType",
        ValuesModelStub([SimpleNamespace(id="type-1", name="变更")]),
    )
    monkeypatch.setattr(
        views,
        "Account",
        ValuesModelStub([SimpleNamespace(id="user-1", display_name="申请人")]),
    )

    body = _payload(await _detail(_request(), "detail"))

    assert body["data"]["ticket_type_name"] == "变更"
    assert body["data"]["creator_name"] == "申请人"


@pytest.mark.asyncio
async def test_todolist_uses_active_m2m_reverse_lookup_and_tenant_filter(monkeypatch):
    now = datetime(2026, 8, 11, 9, 0, 0)
    first = _ticket("first", sla_resolve_at=now)
    second = _ticket("second", sla_resolve_at=now + timedelta(hours=1))
    ticket_model = ModelStub([second, first])
    node_model = ValuesModelStub([
        SimpleNamespace(ticket_id="first"),
        SimpleNamespace(ticket_id="second"),
    ])
    monkeypatch.setattr(views, "Ticket", ticket_model)
    monkeypatch.setattr(views, "TicketNodeInstance", node_model)

    body = _payload(await _todolist(_request()))

    assert body["code"] == 0
    assert [item["id"] for item in body["data"]] == ["first", "second"]
    assert set(body["data"][0]) == {"id", "ticket_no", "title"}
    assert any(
        kwargs.get("approvers__id") == "user-1"
        and kwargs.get("tenant_id") == "tenant-1"
        and kwargs.get("deleted") is False
        for kwargs in _filter_kwargs(node_model.calls)
    )
    assert {
        "id__in": ["first", "second"],
        "tenant_id": "tenant-1",
        "deleted": False,
        "status": TICKET_STATUS_PROCESSING,
    } in (
        _filter_kwargs(ticket_model.calls)
    )


@pytest.mark.asyncio
async def test_todolist_excludes_terminal_ticket_with_stale_active_node(monkeypatch):
    processing = _ticket("processing", status=TICKET_STATUS_PROCESSING)
    terminal = _ticket("terminal", status=TICKET_STATUS_COMPLETED)
    ticket_model = ModelStub([processing, terminal])
    node_model = ValuesModelStub([
        SimpleNamespace(ticket_id="processing"),
        SimpleNamespace(ticket_id="terminal"),
    ])
    monkeypatch.setattr(views, "Ticket", ticket_model)
    monkeypatch.setattr(views, "TicketNodeInstance", node_model)

    body = _payload(await _todolist(_request()))

    assert [item["id"] for item in body["data"]] == ["processing"]
    assert any(
        name == "limit" and args == (20,)
        for name, args, _kwargs in ticket_model.calls
    )


@pytest.mark.asyncio
async def test_todolist_returns_code_zero_and_empty_array_on_any_exception(monkeypatch):
    monkeypatch.setattr(
        views,
        "TicketNodeInstance",
        ValuesModelStub(error=RuntimeError("database unavailable")),
    )

    body = _payload(await _todolist(_request()))

    assert body == {"code": 0, "data": []}


@pytest.mark.asyncio
async def test_todolist_empty_node_ids_skips_ticket_query(monkeypatch):
    ticket_model = ExplodingModel(AssertionError("Ticket.filter must not be called"))
    monkeypatch.setattr(views, "TicketNodeInstance", ValuesModelStub())
    monkeypatch.setattr(views, "Ticket", ticket_model)

    body = _payload(await _todolist(_request()))

    assert body == {"code": 0, "data": []}
    assert ticket_model.calls == []


@pytest.mark.asyncio
async def test_todolist_ticket_query_exception_is_also_suppressed(monkeypatch):
    monkeypatch.setattr(
        views,
        "TicketNodeInstance",
        ValuesModelStub([SimpleNamespace(ticket_id="t1")]),
    )
    monkeypatch.setattr(views, "Ticket", ExplodingModel(RuntimeError("ticket query failed")))

    body = _payload(await _todolist(_request()))

    assert body == {"code": 0, "data": []}
