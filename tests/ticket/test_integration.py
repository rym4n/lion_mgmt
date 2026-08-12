# coding=utf-8
"""工单端到端集成测试（假模型层）。

用同一套内存模型桩串起提单、会签、二级审批和终态归档，并验证
SPEC §4.8 的四条不变量。真实数据库锁与 claim 互斥留给 UAT。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import models
from models.ticket import (
    APPROVE_TYPE_ALL,
    NODE_STATUS_ABNORMAL,
    NODE_STATUS_APPROVED,
    NODE_STATUS_PENDING,
    NODE_STATUS_REJECTED,
    NODE_STATUS_TIMEOUT,
    TICKET_STATUS_COMPLETED,
    TICKET_STATUS_PROCESSING,
    TICKET_STATUS_SUSPENDED,
    TICKET_TERMINAL_STATUSES,
)
from services.ticket import callbacks, engine, manager, merge, validators
from tests.ticket.conftest import FakeQuery, make_account, make_node, make_ticket


TWO_STEP = {
    "nodes": [
        {"id": "start", "type": "start"},
        {
            "id": "ap1",
            "type": "approve",
            "approve_type": APPROVE_TYPE_ALL,
            "approvers": ["a", "b"],
        },
        {"id": "ap2", "type": "approve", "approvers": ["c"]},
        {"id": "end_ok", "type": "end", "terminal_status": TICKET_STATUS_COMPLETED},
        {"id": "end_no", "type": "end", "terminal_status": 2},
    ],
    "edges": [
        {"source": "start", "target": "ap1"},
        {"source": "ap1", "target": "ap2", "on": "approve"},
        {"source": "ap1", "target": "end_no", "on": "reject"},
        {"source": "ap2", "target": "end_ok", "on": "approve"},
        {"source": "ap2", "target": "end_no", "on": "reject"},
    ],
}


def _install_lifecycle_fakes(monkeypatch):
    """让 manager 与 engine 共享同一套内存工单、节点和活动记录。"""
    tickets = []
    nodes = []
    activities = []
    accounts = {account_id: make_account(account_id) for account_id in ("a", "b", "c")}
    version = SimpleNamespace(
        id="ver-1",
        ticket_flow_id="flow-1",
        tenant_id="tenant-1",
        status=1,
        deleted=False,
        flow_json=TWO_STEP,
        form_schema={"fields": [{"key": "reason", "required": True}]},
    )
    ticket_type = SimpleNamespace(
        id="type-1",
        code="CHG",
        default_flow_id="flow-1",
        sla_config={"1": {"respond": 900, "resolve": 7200}},
    )

    class FakeTicket:
        @staticmethod
        def filter(**kwargs):
            rows = [
                ticket for ticket in tickets
                if kwargs.get("id", ticket.id) == ticket.id
                and kwargs.get("tenant_id", ticket.tenant_id) == ticket.tenant_id
                and kwargs.get("deleted", ticket.deleted) == ticket.deleted
            ]
            return FakeQuery(rows)

        @staticmethod
        async def create(**kwargs):
            ticket = make_ticket(
                ticket_id="ticket-1",
                status=kwargs["status"],
                source=kwargs["source"],
                priority=kwargs["priority"],
                tenant_id=kwargs["tenant_id"],
                flow_json=TWO_STEP,
                form_data=kwargs["form_data"],
                sla_respond_at=kwargs["sla_respond_at"],
                sla_resolve_at=kwargs["sla_resolve_at"],
            )
            for field, value in kwargs.items():
                if field != "using_db":
                    setattr(ticket, field, value)
            tickets.append(ticket)
            return ticket

    class FakeNodeInstance:
        @staticmethod
        def filter(**kwargs):
            rows = list(nodes)
            for field in ("id", "ticket_id", "tenant_id", "node_id", "fork_id", "status"):
                if field in kwargs:
                    rows = [node for node in rows if getattr(node, field) == kwargs[field]]
            if "status__in" in kwargs:
                rows = [node for node in rows if node.status in kwargs["status__in"]]
            if "deleted" in kwargs:
                rows = [node for node in rows if node.deleted == kwargs["deleted"]]
            return FakeQuery(rows)

        @staticmethod
        async def create(**kwargs):
            node = make_node(
                node_id=kwargs["node_id"],
                status=kwargs["status"],
                approve_type=kwargs["approve_type"],
                node_type=kwargs["node_type"],
                ticket_id=kwargs["ticket_id"],
                fork_id=kwargs.get("fork_id"),
                branch_key=kwargs.get("branch_key"),
                deadline=kwargs.get("deadline"),
                entered_at=kwargs.get("entered_at"),
                tenant_id=kwargs["tenant_id"],
            )
            node.id = f"inst-{len(nodes) + 1}"
            node.node_name = kwargs["node_name"]
            node.form_data = kwargs["form_data"]
            nodes.append(node)
            return node

    class FakeAccount:
        @staticmethod
        def filter(**kwargs):
            ids = kwargs.get("id__in", ())
            rows = [
                accounts[account_id] for account_id in ids
                if account_id in accounts
                and accounts[account_id].tenant_id == kwargs.get("tenant_id")
            ]
            return FakeQuery(rows)

    @asynccontextmanager
    async def fake_transaction():
        yield None

    async def fake_ticket_no(*_args, **_kwargs):
        return "CHG-20260810-0001"

    async def record_activity(ticket, action, **kwargs):
        activities.append({"ticket_id": ticket.id, "action": action, **kwargs})

    async def closed_callback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(manager, "TicketType", type("M", (), {
        "filter": staticmethod(lambda **_kwargs: FakeQuery([ticket_type])),
    }))
    monkeypatch.setattr(engine, "TicketType", type("M", (), {
        "filter": staticmethod(lambda **_kwargs: FakeQuery([ticket_type])),
    }))

    class FakeFlowVersion:
        @staticmethod
        def filter(**kwargs):
            rows = [version]
            for field in ("id", "ticket_flow_id", "tenant_id", "status", "deleted"):
                if field in kwargs:
                    rows = [
                        row for row in rows
                        if getattr(row, field, None) == kwargs[field]
                    ]
            return FakeQuery(rows)

    fake_flow_version = FakeFlowVersion
    monkeypatch.setattr(manager, "TicketFlowVersion", fake_flow_version)
    monkeypatch.setattr(manager, "Ticket", FakeTicket)
    monkeypatch.setattr(manager, "generate_ticket_no", fake_ticket_no)
    monkeypatch.setattr(manager, "in_transaction", fake_transaction)
    monkeypatch.setattr(manager, "record_activity", record_activity)
    monkeypatch.setattr(manager, "activate_nodes", engine.activate_nodes)

    monkeypatch.setattr(engine, "Ticket", FakeTicket)
    monkeypatch.setattr(engine, "TicketFlowVersion", fake_flow_version)
    monkeypatch.setattr(engine, "TicketNodeInstance", FakeNodeInstance)
    monkeypatch.setattr(engine, "in_transaction", fake_transaction)
    monkeypatch.setattr(engine, "record_activity", record_activity)
    monkeypatch.setattr(models, "Account", FakeAccount)
    monkeypatch.setattr(callbacks, "on_ticket_closed", closed_callback)

    return SimpleNamespace(tickets=tickets, nodes=nodes, activities=activities)


@pytest.mark.asyncio
async def test_submit_approve_and_archive_end_to_end(monkeypatch):
    """提单 -> 两人会签 -> 二级审批 -> 完成归档。"""
    store = _install_lifecycle_fakes(monkeypatch)

    ticket = await manager.create_ticket(
        "tenant-1",
        "acc-creator",
        {
            "ticket_type_id": "type-1",
            "title": "生产变更",
            "priority": 1,
            "form_data": {"reason": "扩容"},
        },
    )

    assert ticket.status == TICKET_STATUS_PROCESSING
    assert ticket.ticket_no == "CHG-20260810-0001"
    ap1 = next(node for node in store.nodes if node.node_id == "ap1")
    assert ap1.status == NODE_STATUS_PENDING
    assert [account.id for account in await ap1.approvers.all()] == ["a", "b"]

    # 注入一条已不属于当前审批人的旧结果，第一次操作必须先清理它。
    ap1.results = {"stale": {"result": "approve"}}
    assert await engine.advance_ticket(
        ticket.id,
        ticket.tenant_id,
        "operate",
        actor_id="a",
        node_instance_id=ap1.id,
        decision="approve",
    ) is True
    assert ap1.status == NODE_STATUS_PENDING
    assert set(ap1.results) == {"a"}
    assert set(ap1.results) <= {account.id for account in await ap1.approvers.all()}
    assert not any(node.node_id == "ap2" for node in store.nodes)

    assert await engine.advance_ticket(
        ticket.id,
        ticket.tenant_id,
        "operate",
        actor_id="b",
        node_instance_id=ap1.id,
        decision="approve",
    ) is True
    assert ap1.status == NODE_STATUS_APPROVED

    ap2 = next(node for node in store.nodes if node.node_id == "ap2")
    assert ap2.status == NODE_STATUS_PENDING
    assert [account.id for account in await ap2.approvers.all()] == ["c"]

    assert await engine.advance_ticket(
        ticket.id,
        ticket.tenant_id,
        "operate",
        actor_id="c",
        node_instance_id=ap2.id,
        decision="approve",
    ) is True
    assert ap2.status == NODE_STATUS_APPROVED
    assert ticket.status == TICKET_STATUS_COMPLETED
    assert ticket.closed_at is not None
    assert store.activities[0]["action"] == "submit"
    assert store.activities[-1]["content"] == "工单已归档"

    # 终态守卫也要在完整链路上成立，旧节点不能再次推进已归档工单。
    assert await engine.advance_ticket(
        ticket.id,
        ticket.tenant_id,
        "operate",
        actor_id="c",
        node_instance_id=ap2.id,
        decision="approve",
    ) is False


def test_rejection_and_timeout_route_to_reject_end():
    """拒绝和超时都必须走 reject 边，不能映射成 approve。"""
    for node_id in ("ap1", "ap2"):
        for outcome in (NODE_STATUS_REJECTED, NODE_STATUS_TIMEOUT):
            assert engine.next_node_ids(TWO_STEP, node_id, outcome, {}) == ["end_no"]


def test_invariant_results_are_subset_of_current_approvers():
    cleaned = validators.purge_stale_results(
        ["a", "a", "b"],
        {"a": {"result": "approve"}, "stale": {"result": "approve"}},
    )
    assert set(cleaned) <= {"a", "b"}
    assert validators.evaluate_node_outcome(APPROVE_TYPE_ALL, [], {}) is None


@pytest.mark.asyncio
async def test_invariant_abnormal_node_implies_suspended_ticket(monkeypatch):
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING)
    node = make_node(status=NODE_STATUS_PENDING)

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(engine, "record_activity", noop)
    await engine.mark_node_abnormal(node, ticket, reason="test")

    assert node.status == NODE_STATUS_ABNORMAL
    assert ticket.status == TICKET_STATUS_SUSPENDED
    assert (node.status == NODE_STATUS_ABNORMAL) == (
        ticket.status == TICKET_STATUS_SUSPENDED
    )


def test_invariant_terminal_ticket_accepts_no_transition():
    for terminal in TICKET_TERMINAL_STATUSES:
        for target in range(TICKET_STATUS_SUSPENDED + 1):
            assert engine.can_transition_ticket(terminal, target) is False


@pytest.mark.asyncio
async def test_invariant_merge_counts_finished_branches_not_incoming_edges():
    flow = {
        "nodes": [
            {"id": "fork", "type": "parallel", "mode": "fork", "branches": ["x", "y"]},
            {"id": "x1", "type": "approve", "fork_id": "fork", "branch_key": "x"},
            {"id": "x2", "type": "approve", "fork_id": "fork", "branch_key": "x"},
            {"id": "y1", "type": "approve", "fork_id": "fork", "branch_key": "y"},
            {
                "id": "join",
                "type": "parallel",
                "mode": "merge",
                "fork_id": "fork",
                "strategy": "all",
            },
        ],
        "edges": [
            {"source": "fork", "target": "x1"},
            {"source": "fork", "target": "y1"},
            {"source": "x1", "target": "join"},
            {"source": "x2", "target": "join"},
            {"source": "y1", "target": "join"},
        ],
    }
    ticket = make_ticket()
    nodes = [
        make_node(
            node_id="x1", status=NODE_STATUS_APPROVED,
            fork_id="fork", branch_key="x",
        ),
        make_node(
            node_id="x2", status=NODE_STATUS_PENDING,
            fork_id="fork", branch_key="x",
        ),
        make_node(
            node_id="y1", status=NODE_STATUS_APPROVED,
            fork_id="fork", branch_key="y",
        ),
    ]
    merge_def = next(node for node in flow["nodes"] if node["id"] == "join")
    provider = lambda: nodes

    assert merge.fork_branch_count(flow, "fork") == 2
    assert len([edge for edge in flow["edges"] if edge["target"] == "join"]) == 3
    assert await merge.count_finished_branches(
        ticket, merge_def, nodes_provider=provider,
    ) == {"y"}
    assert await merge.is_merge_ready(
        ticket, merge_def, flow, nodes_provider=provider,
    ) is False

    nodes[1].status = NODE_STATUS_APPROVED
    assert await merge.count_finished_branches(
        ticket, merge_def, nodes_provider=provider,
    ) == {"x", "y"}
    assert await merge.is_merge_ready(
        ticket, merge_def, flow, nodes_provider=provider,
    ) is True
