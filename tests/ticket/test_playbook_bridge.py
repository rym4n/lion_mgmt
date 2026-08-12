# coding=utf-8
"""剧本建单和终态回传桥接契约。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from models.ticket import SOURCE_PLAYBOOK, TICKET_STATUS_COMPLETED
from services.ticket import playbook_bridge


@pytest.mark.asyncio
async def test_create_ticket_from_playbook_maps_origin_fields(monkeypatch):
    captured = {}

    async def fake_create(tenant_id, creator_id, payload):
        captured.update(tenant_id=tenant_id, creator_id=creator_id, payload=payload)
        return SimpleNamespace(id="ticket-1")

    monkeypatch.setattr(playbook_bridge, "create_ticket", fake_create)
    ticket = await playbook_bridge.create_ticket_from_playbook(
        "tenant-1",
        "creator-1",
        {
            "run_id": "run-1",
            "flow_id": "flow-version-1",
            "audit_app": "approve-node-1",
            "node_name": "变更审批",
            "params": [{"name": "reason", "value": "扩容"}],
            "callback_token": "callback-1",
        },
        {"properties": {"ticket": {"ticket_type_id": "type-1", "priority": 2}}},
    )

    assert ticket.id == "ticket-1"
    assert captured["tenant_id"] == "tenant-1"
    assert captured["creator_id"] == "creator-1"
    assert captured["payload"] == {
        "ticket_type_id": "type-1",
        "title": "变更审批",
        "priority": 2,
        "form_data": {"reason": "扩容"},
        "source": SOURCE_PLAYBOOK,
        "origin_run_id": "run-1",
        "origin_node_id": "approve-node-1",
        "origin_flow_id": "flow-version-1",
        "origin_params": {"reason": "扩容"},
        "callback_token": "callback-1",
    }


def test_parse_approvers_supports_users_roles_and_deduplication():
    assert playbook_bridge.parse_approver_config({
        "approvers": "user-1,user-2,user-1",
        "roles": ["role-1", "role-1"],
    }) == {
        "users": ["user-1", "user-2"],
        "roles": ["role-1"],
    }


@pytest.mark.asyncio
async def test_callback_playbook_publishes_ten_field_contract(monkeypatch):
    published = []

    async def fake_publish(topic, message):
        published.append((topic, message))

    monkeypatch.setattr(playbook_bridge, "aproduce", fake_publish)
    monkeypatch.setattr(
        playbook_bridge.TicketNodeInstance,
        "filter",
        lambda **_kwargs: _NodeQuery([]),
    )
    ticket = SimpleNamespace(
        id="ticket-1",
        ticket_no="CHG-20260811-0001",
        status=TICKET_STATUS_COMPLETED,
        title="生产扩容",
        creator_id="creator-1",
        closed_at=datetime(2026, 8, 11, 12, 30, 0),
        origin_run_id="run-1",
        origin_flow_id="flow-version-1",
        origin_node_id="approve-node-1",
        origin_params={"reason": "扩容"},
        tenant_id="tenant-1",
    )

    payload = await playbook_bridge.callback_playbook(
        ticket,
        operator="approver-1",
        approval_result={"approver-1": {"result": "approve"}},
        metadata={"source": "playbook"},
    )

    assert tuple(payload) == playbook_bridge.CALLBACK_FIELDS
    assert len(payload) == 10
    assert payload["status"] == TICKET_STATUS_COMPLETED
    assert payload["completed_at"] == "2026-08-11T12:30:00"
    assert published[0][1]["ticket_result"] == payload
    assert published[0][1]["run_args"]["flow_input"]["approval_result"] == payload
    assert published[0][1]["run_args"]["approval_status"] == 1


class _NodeQuery:
    def __init__(self, rows):
        self.rows = rows

    def order_by(self, *_args):
        return self

    async def all(self):
        return self.rows
