# coding=utf-8
"""建单链路。串起单号、SLA、版本钉住、起始节点激活四件事。"""

from datetime import datetime, timedelta

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.ticket import (
    SOURCE_PLAYBOOK,
    TICKET_STATUS_PROCESSING,
)
from services.ticket import manager
from tests.ticket.conftest import FakeQuery, make_ticket

FLOW_JSON = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "ap1", "type": "approve", "approvers": ["acc-a"], "approve_type": 1},
        {"id": "end_ok", "type": "end", "terminal_status": 1},
    ],
    "edges": [
        {"source": "start", "target": "ap1"},
        {"source": "ap1", "target": "end_ok", "on": "approve"},
    ],
}


def _install(
    monkeypatch, *, published=True, sla_config=None, default_flow=True,
    flow_json=FLOW_JSON, version_status=None,
):
    """装一套建单所需的假模型。"""
    from types import SimpleNamespace

    created = {}
    version = SimpleNamespace(
        id="ver-1",
        status=(1 if published else 0) if version_status is None else version_status,
        flow_json=flow_json,
        form_schema={"fields": [{"key": "amount", "required": True}]},
        ticket_flow_id="flow-1",
    )
    ticket_type = SimpleNamespace(
        id="type-1", code="CHG", name="变更", deleted=False, tenant_id="tenant-1",
        default_flow_id="flow-1" if default_flow else None,
        sla_config=sla_config if sla_config is not None else {"1": {"respond": 900, "resolve": 7200}},
    )
    flow = SimpleNamespace(
        id="flow-1", ticket_type_id="type-1", deleted=False,
        tenant_id="tenant-1",
    )

    monkeypatch.setattr(manager, "TicketType", type("M", (), {
        "filter": staticmethod(lambda **k: FakeQuery([ticket_type] if k.get("id") == "type-1" else []))
    }))
    monkeypatch.setattr(manager, "TicketFlowVersion", type("M", (), {
        "filter": staticmethod(lambda **k: FakeQuery([version] if published else []))
    }))
    monkeypatch.setattr(manager, "TicketFlow", type("M", (), {
        "filter": staticmethod(
            lambda **k: FakeQuery([flow] if k.get("id") == "flow-1" else [])
        )
    }))

    async def _create(**kwargs):
        created.update(kwargs)
        return make_ticket(
            ticket_id="t-new", status=kwargs.get("status", 0),
            source=kwargs.get("source", 0), priority=kwargs.get("priority", 1),
        )

    monkeypatch.setattr(manager, "Ticket", type("M", (), {
        "create": staticmethod(_create),
        "filter": staticmethod(lambda **k: FakeQuery([])),
    }))
    monkeypatch.setattr(manager, "generate_ticket_no",
                        lambda *a, **k: _await_value("CHG-20260810-0001"))
    activated = []
    monkeypatch.setattr(manager, "activate_nodes",
                        lambda t, ids, flow, connection=None: _await_value(activated.extend(ids)))
    monkeypatch.setattr(manager, "record_activity", lambda *a, **k: _await_value(None))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _tx():
        yield None

    monkeypatch.setattr(manager, "in_transaction", _tx)
    return SimpleNamespace(created=created, activated=activated, version=version)


async def _await_value(value):
    return value


@pytest.mark.asyncio
async def test_create_assigns_ticket_no_and_pins_version(monkeypatch):
    ctx = _install(monkeypatch)
    await manager.create_ticket("tenant-1", "acc-creator", {
        "ticket_type_id": "type-1", "title": "换机", "priority": 1,
        "form_data": {"amount": 100},
    })
    assert ctx.created["ticket_no"] == "CHG-20260810-0001"
    assert ctx.created["flow_version_id"] == "ver-1"     # 钉住版本
    assert ctx.created["status"] == TICKET_STATUS_PROCESSING


@pytest.mark.asyncio
async def test_create_supports_explicit_flow_and_current_version(monkeypatch):
    ctx = _install(monkeypatch)

    await manager.create_ticket("tenant-1", "acc-creator", {
        "ticket_flow_id": "flow-1",
        "flow_version_id": "ver-1",
        "title": "换机",
        "form_data": {"amount": 100},
    })

    assert ctx.created["ticket_type_id"] == "type-1"
    assert ctx.created["flow_version_id"] == "ver-1"


@pytest.mark.asyncio
async def test_create_rejects_unknown_explicit_version(monkeypatch):
    _install(monkeypatch, published=False)

    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_flow_id": "flow-1",
            "flow_version_id": "missing-version",
            "title": "换机",
            "form_data": {"amount": 100},
        })

    assert exc.value.enum is ErrorCode.TICKET_FLOW_VALIDATE_FAILED


@pytest.mark.asyncio
async def test_manual_create_rejects_historical_version(monkeypatch):
    _install(monkeypatch, version_status=2)

    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_flow_id": "flow-1",
            "flow_version_id": "ver-1",
            "title": "换机",
            "form_data": {"amount": 100},
        })

    assert exc.value.enum is ErrorCode.TICKET_FLOW_VALIDATE_FAILED


@pytest.mark.asyncio
async def test_playbook_create_can_pin_historical_version(monkeypatch):
    ctx = _install(monkeypatch, version_status=2)

    await manager.create_ticket(
        "tenant-1",
        None,
        {
            "ticket_flow_id": "flow-1",
            "flow_version_id": "ver-1",
            "title": "剧本工单",
            "form_data": {"amount": 100},
            "source": SOURCE_PLAYBOOK,
        },
        allow_historical_version=True,
    )

    assert ctx.created["flow_version_id"] == "ver-1"


@pytest.mark.asyncio
async def test_create_activates_first_node_after_start(monkeypatch):
    ctx = _install(monkeypatch)
    await manager.create_ticket("tenant-1", "acc-creator", {
        "ticket_type_id": "type-1", "title": "换机", "form_data": {"amount": 1},
    })
    assert ctx.activated == ["ap1"]


@pytest.mark.asyncio
async def test_create_computes_sla_from_type_config(monkeypatch):
    ctx = _install(monkeypatch)
    await manager.create_ticket("tenant-1", "acc-creator", {
        "ticket_type_id": "type-1", "title": "换机", "priority": 1,
        "form_data": {"amount": 1},
    })
    assert ctx.created["sla_respond_at"] is not None
    assert ctx.created["sla_resolve_at"] is not None


@pytest.mark.asyncio
async def test_create_leaves_sla_null_when_priority_unconfigured(monkeypatch):
    ctx = _install(monkeypatch, sla_config={"3": {"respond": 900}})
    await manager.create_ticket("tenant-1", "acc-creator", {
        "ticket_type_id": "type-1", "title": "换机", "priority": 0,
        "form_data": {"amount": 1},
    })
    assert ctx.created["sla_respond_at"] is None
    assert ctx.created["sla_resolve_at"] is None


@pytest.mark.asyncio
async def test_create_rejects_type_without_published_version(monkeypatch):
    _install(monkeypatch, published=False)
    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_type_id": "type-1", "title": "换机", "form_data": {"amount": 1},
        })
    assert exc.value.enum is ErrorCode.TICKET_FLOW_NO_PUBLISHED


@pytest.mark.asyncio
async def test_create_rejects_type_without_default_flow(monkeypatch):
    _install(monkeypatch, default_flow=False)
    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_type_id": "type-1", "title": "换机", "form_data": {"amount": 1},
        })
    assert exc.value.enum is ErrorCode.TICKET_TYPE_NO_FLOW


@pytest.mark.asyncio
async def test_create_rejects_unknown_type(monkeypatch):
    _install(monkeypatch)
    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_type_id": "ghost", "title": "换机", "form_data": {},
        })
    assert exc.value.enum is ErrorCode.TICKET_TYPE_NOT_FOUND


@pytest.mark.asyncio
async def test_create_rejects_missing_required_form_field(monkeypatch):
    _install(monkeypatch)
    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_type_id": "type-1", "title": "换机", "form_data": {},
        })
    assert exc.value.enum is ErrorCode.TICKET_FORM_INVALID


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, False], ids=["zero", "false"])
async def test_create_accepts_falsy_required_form_values(monkeypatch, value):
    ctx = _install(monkeypatch)
    await manager.create_ticket("tenant-1", "acc-creator", {
        "ticket_type_id": "type-1", "title": "换机", "form_data": {"amount": value},
    })
    assert ctx.created["form_data"]["amount"] is value


@pytest.mark.asyncio
async def test_create_rejects_empty_string_required_form_field(monkeypatch):
    _install(monkeypatch)
    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_type_id": "type-1", "title": "换机", "form_data": {"amount": ""},
        })
    assert exc.value.enum is ErrorCode.TICKET_FORM_INVALID


@pytest.mark.asyncio
async def test_create_rejects_flow_without_start_and_does_not_create_ticket(monkeypatch):
    flow_json = {
        "nodes": [{"id": "ap1", "type": "approve", "approvers": ["acc-a"]}],
        "edges": [],
    }
    ctx = _install(monkeypatch, flow_json=flow_json)
    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_type_id": "type-1", "title": "换机", "form_data": {"amount": 1},
        })
    assert exc.value.enum is ErrorCode.TICKET_FLOW_VALIDATE_FAILED
    assert ctx.created == {}
    assert ctx.activated == []


@pytest.mark.asyncio
async def test_create_rejects_start_without_targets(monkeypatch):
    flow_json = {"nodes": [{"id": "start", "type": "start"}], "edges": []}
    ctx = _install(monkeypatch, flow_json=flow_json)
    with pytest.raises(CustomError) as exc:
        await manager.create_ticket("tenant-1", "acc-creator", {
            "ticket_type_id": "type-1", "title": "换机", "form_data": {"amount": 1},
        })
    assert exc.value.enum is ErrorCode.TICKET_FLOW_VALIDATE_FAILED
    assert ctx.created == {}
    assert ctx.activated == []


@pytest.mark.asyncio
async def test_playbook_source_persists_origin_fields(monkeypatch):
    """剧本触发必须存全 origin_*，终态时才能重建 10 字段恢复消息。"""
    ctx = _install(monkeypatch)
    await manager.create_ticket("tenant-1", None, {
        "ticket_type_id": "type-1", "title": "剧本审批", "form_data": {"amount": 1},
        "source": SOURCE_PLAYBOOK, "origin_run_id": "run-9",
        "origin_node_id": "node-9", "origin_flow_id": "flow-9",
        "origin_params": {"approval_kind": "playbook"},
    })
    for field in ("origin_run_id", "origin_node_id", "origin_flow_id", "origin_params"):
        assert ctx.created[field] is not None
    assert ctx.created["source"] == SOURCE_PLAYBOOK


@pytest.mark.asyncio
async def test_playbook_flow_timeout_caps_resolve(monkeypatch):
    """SPEC §2.2：剧本剩余 flow_timeout 更早时，它是上界。"""
    ctx = _install(monkeypatch)
    deadline = datetime.now(manager.TZ) + timedelta(seconds=120)
    await manager.create_ticket("tenant-1", None, {
        "ticket_type_id": "type-1", "title": "剧本审批", "form_data": {"amount": 1},
        "source": SOURCE_PLAYBOOK, "flow_deadline": deadline,
    })
    assert ctx.created["sla_resolve_at"] == deadline
