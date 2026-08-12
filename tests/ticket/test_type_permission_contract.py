# coding=utf-8
"""Ticket type endpoints use the list/read and flow/update permissions."""

import ast
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from services.ticket import views


VIEWS = Path(__file__).resolve().parents[2] / "services/ticket/views.py"


def _permission(function_name: str) -> str:
    tree = ast.parse(VIEWS.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    decorator = next(
        item
        for item in function.decorator_list
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "requires_permission"
    )
    return decorator.args[0].value


def test_ticket_type_list_requires_ticket_read():
    assert _permission("type_list_view") == "ticket:r"


def test_ticket_type_save_requires_ticket_flow_update():
    assert _permission("type_save_view") == "ticket_flow:u"


class _Query:
    def __init__(self, rows, calls):
        self.rows = list(rows)
        self.calls = calls

    def filter(self, **kwargs):
        self.calls.append(("filter", kwargs))
        return self

    def order_by(self, *fields):
        self.calls.append(("order_by", fields))
        return self

    async def all(self):
        return self.rows


class _Model:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def filter(self, **kwargs):
        return _Query(self.rows, self.calls).filter(**kwargs)


@pytest.mark.asyncio
async def test_ticket_type_list_embeds_only_latest_published_form_schema(
    monkeypatch,
):
    no_flow = SimpleNamespace(
        default_flow_id=None,
        json={"id": "type-1", "name": "无流程", "default_flow_id": None},
    )
    published = SimpleNamespace(
        default_flow_id="flow-1",
        json={"id": "type-2", "name": "已发布", "default_flow_id": "flow-1"},
    )
    unpublished = SimpleNamespace(
        default_flow_id="flow-2",
        json={"id": "type-3", "name": "未发布", "default_flow_id": "flow-2"},
    )
    latest = SimpleNamespace(
        id="version-new",
        ticket_flow_id="flow-1",
        version="v2",
        status=1,
        form_schema={"fields": [{"key": "reason"}]},
    )
    older = SimpleNamespace(
        id="version-old",
        ticket_flow_id="flow-1",
        version="v1",
        status=1,
        form_schema={"fields": [{"key": "legacy"}]},
    )
    type_model = _Model([no_flow, published, unpublished])
    version_model = _Model([latest, older])
    monkeypatch.setattr(views, "TicketType", type_model)
    monkeypatch.setattr(views, "TicketFlowVersion", version_model)
    request = SimpleNamespace(ctx=SimpleNamespace(session={"tenant_id": "tenant-1"}))

    response = await unwrap(views.type_list_view)(request)
    payload = orjson.loads(response.body)["data"]

    assert payload[0]["default_flow"] is None
    assert payload[1]["default_flow"] == {
        "id": "flow-1",
        "published_version": {
            "id": "version-new",
            "version": "v2",
            "status": 1,
            "form_schema": {"fields": [{"key": "reason"}]},
        },
    }
    assert payload[2]["default_flow"] == {
        "id": "flow-2",
        "published_version": None,
    }
    assert "flow_json" not in payload[1]["default_flow"]["published_version"]
    assert type_model.calls[0] == (
        "filter",
        {"tenant_id": "tenant-1", "deleted": False},
    )
    assert version_model.calls[0] == (
        "filter",
        {
            "ticket_flow_id__in": {"flow-1", "flow-2"},
            "tenant_id": "tenant-1",
            "status": 1,
            "deleted": False,
        },
    )
    assert version_model.calls[1] == ("order_by", ("-ctime",))
