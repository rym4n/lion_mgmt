# coding=utf-8
"""SLA wiring for ticket creation and workflow advancement."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import models
from models.ticket import (
    APPROVE_TYPE_ANY,
    NODE_STATUS_APPROVED,
    TICKET_STATUS_COMPLETED,
    TICKET_STATUS_PROCESSING,
)
from services.ticket import engine, manager
from tests.ticket.conftest import FakeQuery, make_account, make_node, make_ticket
from tests.ticket.test_create_flow import _install as install_create_fakes
from tests.ticket.test_state_machine import _install_engine_fakes


NON_TERMINAL_FLOW = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "ap1", "type": "approve"},
        {"id": "ap2", "type": "approve", "approvers": ["b"]},
    ],
    "edges": [
        {"source": "start", "target": "ap1"},
        {"source": "ap1", "target": "ap2", "on": "approve"},
    ],
}

TERMINAL_FLOW = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "ap1", "type": "approve"},
        {
            "id": "end_ok",
            "type": "end",
            "terminal_status": TICKET_STATUS_COMPLETED,
        },
    ],
    "edges": [
        {"source": "start", "target": "ap1"},
        {"source": "ap1", "target": "end_ok", "on": "approve"},
    ],
}


def _install_ticket_type(monkeypatch, sla_config):
    ticket_type = SimpleNamespace(id="type-1", sla_config=sla_config)

    class FakeTicketType:
        @staticmethod
        def filter(**kwargs):
            matches = (
                kwargs.get("id") == ticket_type.id
                and kwargs.get("tenant_id") == "tenant-1"
                and kwargs.get("deleted") is False
            )
            return FakeQuery([ticket_type] if matches else [])

    monkeypatch.setattr(engine, "TicketType", FakeTicketType)


@pytest.mark.asyncio
async def test_create_calculates_sla(monkeypatch):
    respond_at = datetime(2026, 8, 11, 10, 0, 0)
    resolve_at = datetime(2026, 8, 11, 12, 0, 0)
    ctx = install_create_fakes(monkeypatch)
    monkeypatch.setattr(
        manager,
        "calculate_sla",
        lambda *_args, **_kwargs: (respond_at, resolve_at),
    )

    await manager.create_ticket(
        "tenant-1",
        "acc-creator",
        {
            "ticket_type_id": "type-1",
            "form_data": {"amount": 1},
        },
    )

    assert ctx.created["sla_respond_at"] == respond_at
    assert ctx.created["sla_resolve_at"] == resolve_at


@pytest.mark.asyncio
async def test_advance_updates_sla_for_non_terminal_ticket(monkeypatch):
    created_at = datetime(2026, 8, 11, 9, 0, 0)
    ticket = make_ticket(
        status=TICKET_STATUS_PROCESSING,
        ctime=created_at,
        sla_respond_at=None,
        sla_resolve_at=None,
    )
    node = make_node(
        node_id="ap1",
        approve_type=APPROVE_TYPE_ANY,
        approver_ids=("a",),
    )
    _install_engine_fakes(monkeypatch, ticket, [node], flow=NON_TERMINAL_FLOW)
    _install_ticket_type(
        monkeypatch,
        {"respond": 900, "resolve": 7200},
    )

    account_b = make_account("b")

    class FakeAccount:
        @staticmethod
        def filter(**_kwargs):
            return FakeQuery([account_b])

    monkeypatch.setattr(models, "Account", FakeAccount)

    assert await engine.advance_ticket(
        ticket.id,
        ticket.tenant_id,
        "operate",
        actor_id="a",
        node_instance_id=node.id,
        decision="approve",
    ) is True

    assert node.status == NODE_STATUS_APPROVED
    assert ticket.status == TICKET_STATUS_PROCESSING
    assert ticket.sla_respond_at == created_at + timedelta(seconds=900)
    assert ticket.sla_resolve_at == created_at + timedelta(seconds=7200)
    assert ticket.saved[-1] == ("sla_respond_at", "sla_resolve_at", "utime")


@pytest.mark.asyncio
async def test_advance_does_not_update_sla_for_terminal_ticket(monkeypatch):
    original_respond_at = datetime(2026, 8, 11, 9, 15, 0)
    original_resolve_at = datetime(2026, 8, 11, 11, 0, 0)
    ticket = make_ticket(
        status=TICKET_STATUS_PROCESSING,
        sla_respond_at=original_respond_at,
        sla_resolve_at=original_resolve_at,
    )
    node = make_node(
        node_id="ap1",
        approve_type=APPROVE_TYPE_ANY,
        approver_ids=("a",),
    )
    _install_engine_fakes(monkeypatch, ticket, [node], flow=TERMINAL_FLOW)
    _install_ticket_type(
        monkeypatch,
        {"respond": 1, "resolve": 1},
    )

    assert await engine.advance_ticket(
        ticket.id,
        ticket.tenant_id,
        "operate",
        actor_id="a",
        node_instance_id=node.id,
        decision="approve",
    ) is True

    assert ticket.status == TICKET_STATUS_COMPLETED
    assert ticket.sla_respond_at == original_respond_at
    assert ticket.sla_resolve_at == original_resolve_at
    assert all("sla_respond_at" not in fields for fields in ticket.saved)
