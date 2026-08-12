# coding=utf-8
from datetime import datetime
import inspect
from types import SimpleNamespace

import pytest

from services.ticket import views
from settings import TZ
from tests.ticket.conftest import FakeQuery, make_account, make_node, make_ticket


def _ticket_with_employee(enabled: bool, employee_id: str = "employee-1"):
    return make_ticket(
        flow_json={
            "digital_employee": {
                "enabled": enabled,
                "employee_id": employee_id,
            }
        }
    )


def _session():
    return {
        "tenant_id": "tenant-1",
        "sub": "approver-1",
        "account_info": make_account("approver-1"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_action_view_notifies_after_approval_decision(monkeypatch, action):
    ticket = _ticket_with_employee(True)
    node = make_node("approval", approver_ids=["approver-1"])
    events = []

    monkeypatch.setattr(views.Ticket, "filter", lambda **kwargs: FakeQuery([ticket]))
    monkeypatch.setattr(
        views.TicketNodeInstance,
        "filter",
        lambda **kwargs: FakeQuery([node]),
    )

    async def mock_is_current_approver(current_node, user_id):
        return current_node is node and user_id == "approver-1"

    async def mock_advance_ticket(**kwargs):
        events.append(("advance", kwargs["decision"]))

    async def mock_notify(current_ticket, decision, session, comment):
        events.append(("notify", decision, comment))

    monkeypatch.setattr(
        "services.ticket.permissions.is_current_approver",
        mock_is_current_approver,
    )
    monkeypatch.setattr(views, "advance_ticket", mock_advance_ticket)
    monkeypatch.setattr(views, "_notify_digital_employee", mock_notify)

    request = SimpleNamespace(
        ctx=SimpleNamespace(session=_session()),
        json={
            "ticket_id": ticket.id,
            "node_instance_id": node.id,
            "action": action,
            "comment": "审批意见",
        },
    )

    await inspect.unwrap(views.action_view)(request)

    assert events == [
        ("advance", action),
        ("notify", action, "审批意见"),
    ]


@pytest.mark.asyncio
async def test_disabled_digital_employee_does_not_receive_approval(monkeypatch):
    calls = []

    async def mock_chat_with_agent(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(views, "chat_with_agent", mock_chat_with_agent)

    await views._notify_digital_employee(
        _ticket_with_employee(False), "approve", _session(), ""
    )

    assert calls == []


@pytest.mark.asyncio
async def test_enabled_digital_employee_receives_approve_notification(monkeypatch):
    calls = []

    async def mock_chat_with_agent(message, **kwargs):
        calls.append((message, kwargs))

    monkeypatch.setattr(views, "chat_with_agent", mock_chat_with_agent)
    ticket = _ticket_with_employee(True)

    await views._notify_digital_employee(ticket, "approve", _session(), "同意执行")

    assert len(calls) == 1
    message, kwargs = calls[0]
    assert ticket.ticket_no in message
    assert ticket.title in message
    assert "审批结果：通过" in message
    assert "审批人：用户approver-1" in message
    assert "时间：" in message
    assert kwargs == {
        "employee_id": "employee-1",
        "tenant_id": "tenant-1",
        "session_id": "ticket:t1:approval",
    }


@pytest.mark.asyncio
async def test_enabled_digital_employee_receives_reject_reason(monkeypatch):
    messages = []

    async def mock_chat_with_agent(message, **kwargs):
        messages.append(message)

    monkeypatch.setattr(views, "chat_with_agent", mock_chat_with_agent)

    await views._notify_digital_employee(
        _ticket_with_employee(True), "reject", _session(), "缺少变更回退方案"
    )

    assert len(messages) == 1
    assert "审批结果：拒绝" in messages[0]
    assert "拒绝原因：缺少变更回退方案" in messages[0]
    assert "审批人：用户approver-1" in messages[0]


def test_approval_notification_uses_timezone_aware_time():
    message = views._approval_notification(
        _ticket_with_employee(True),
        "approve",
        "审批员",
        "",
        datetime(2026, 8, 11, 15, 30, tzinfo=TZ),
    )

    assert "时间：2026-08-11T15:30:00" in message


def test_nested_legacy_flow_config_remains_supported():
    ticket = make_ticket(
        flow_json={
            "settings": {
                "digital_employee_enabled": True,
                "employee_id": "employee-legacy",
            }
        }
    )

    assert views._digital_employee_config(ticket) == {
        "enabled": True,
        "employee_id": "employee-legacy",
    }
