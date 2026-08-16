# coding=utf-8
"""状态机。转移合法性与下一跳计算是纯函数，优先用它们锁住语义；
带事务的 advance_ticket 用 monkeypatch 驱动（行锁本身靠 UAT 验证）。"""

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.ticket import (
    APPROVE_TYPE_ALL,
    APPROVE_TYPE_ANY,
    NODE_STATUS_ABNORMAL,
    NODE_STATUS_APPROVED,
    NODE_STATUS_PENDING,
    NODE_STATUS_REJECTED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_TIMEOUT,
    RESULT_KEY_CANCELLED,
    TICKET_STATUS_COMPLETED,
    TICKET_STATUS_PROCESSING,
    TICKET_STATUS_REJECTED,
    TICKET_STATUS_SUSPENDED,
)
from services.ticket import engine
from tests.ticket.conftest import FakeQuery, make_account, make_node, make_ticket


# ---------- 状态转移表（SPEC §4.8）----------

@pytest.mark.parametrize("target", [1, 2, 3, 4, 5, 6])
def test_processing_can_reach_every_other_state(target):
    assert engine.can_transition_ticket(TICKET_STATUS_PROCESSING, target) is True


@pytest.mark.parametrize("terminal", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("target", [0, 1, 2, 3, 4, 5, 6])
def test_terminal_ticket_states_are_absorbing(terminal, target):
    """终态是吸收态：任何状态变更都必须被拒绝。"""
    assert engine.can_transition_ticket(terminal, target) is False


def test_suspended_can_only_recover_or_force_close():
    assert engine.can_transition_ticket(TICKET_STATUS_SUSPENDED, TICKET_STATUS_PROCESSING) is True
    assert engine.can_transition_ticket(TICKET_STATUS_SUSPENDED, 5) is True
    assert engine.can_transition_ticket(TICKET_STATUS_SUSPENDED, TICKET_STATUS_COMPLETED) is False


def test_node_pending_transitions():
    for target in (NODE_STATUS_APPROVED, NODE_STATUS_REJECTED,
                   NODE_STATUS_TIMEOUT, NODE_STATUS_ABNORMAL):
        assert engine.can_transition_node(NODE_STATUS_PENDING, target) is True
    # 待处理不能直接跳到执行中——自动节点激活时就直接进 4，不经 0
    assert engine.can_transition_node(NODE_STATUS_PENDING, NODE_STATUS_RUNNING) is False


def test_node_running_transitions():
    for target in (NODE_STATUS_APPROVED, NODE_STATUS_REJECTED, NODE_STATUS_ABNORMAL):
        assert engine.can_transition_node(NODE_STATUS_RUNNING, target) is True
    assert engine.can_transition_node(NODE_STATUS_RUNNING, NODE_STATUS_TIMEOUT) is False


def test_node_abnormal_recovers_to_pending_only():
    assert engine.can_transition_node(NODE_STATUS_ABNORMAL, NODE_STATUS_PENDING) is True
    assert engine.can_transition_node(NODE_STATUS_ABNORMAL, NODE_STATUS_APPROVED) is False


@pytest.mark.parametrize("terminal", [NODE_STATUS_APPROVED, NODE_STATUS_REJECTED, NODE_STATUS_TIMEOUT])
def test_node_terminal_states_are_absorbing(terminal):
    for target in range(6):
        assert engine.can_transition_node(terminal, target) is False


# ---------- 下一跳计算 ----------

LINEAR = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "ap1", "type": "approve"},
        {"id": "end_ok", "type": "end", "terminal_status": TICKET_STATUS_COMPLETED},
        {"id": "end_no", "type": "end", "terminal_status": TICKET_STATUS_REJECTED},
    ],
    "edges": [
        {"source": "start", "target": "ap1"},
        {"source": "ap1", "target": "end_ok", "on": "approve"},
        {"source": "ap1", "target": "end_no", "on": "reject"},
    ],
}


def test_approved_node_follows_approve_edge():
    assert engine.next_node_ids(LINEAR, "ap1", NODE_STATUS_APPROVED, {}) == ["end_ok"]


def test_rejected_node_follows_reject_edge():
    assert engine.next_node_ids(LINEAR, "ap1", NODE_STATUS_REJECTED, {}) == ["end_no"]


def test_timeout_outcome_routes_like_rejection():
    """SPEC §2.1：非「通过」一律按拒绝走，宁可走拒绝分支也不悬挂。"""
    assert engine.next_node_ids(LINEAR, "ap1", NODE_STATUS_TIMEOUT, {}) == ["end_no"]


CONDITION = {
    "nodes": [
        {"id": "cond", "type": "condition"},
        {"id": "big", "type": "approve"},
        {"id": "small", "type": "approve"},
    ],
    "edges": [
        {"source": "cond", "target": "big",
         "condition": {"field": "amount", "op": "gt", "value": 1000}},
        {"source": "cond", "target": "small", "default": True},
    ],
}


def test_condition_picks_matching_branch():
    assert engine.next_node_ids(CONDITION, "cond", NODE_STATUS_APPROVED,
                                {"amount": 5000}) == ["big"]


def test_condition_falls_back_to_default_branch():
    assert engine.next_node_ids(CONDITION, "cond", NODE_STATUS_APPROVED,
                                {"amount": 10}) == ["small"]


def test_condition_picks_exactly_one_edge():
    """条件分支必须选唯一一条出边，多选会把工单分叉成假并行。"""
    result = engine.next_node_ids(CONDITION, "cond", NODE_STATUS_APPROVED,
                                  {"amount": 5000})
    assert len(result) == 1


def test_missing_field_uses_default_branch():
    """表单字段缺失时不能炸，也不能什么都不选（否则工单静默停滞）。"""
    assert engine.next_node_ids(CONDITION, "cond", NODE_STATUS_APPROVED, {}) == ["small"]


def test_approval_node_form_validates_required_fields_and_unknown_keys():
    definition = {
        "id": "ap1",
        "type": "approve",
        "form_fields": [
            {"key": "review_note", "label": "审批说明", "required": True},
            {"key": "risk", "label": "风险", "required": False},
        ],
    }

    with pytest.raises(CustomError) as exc:
        engine.validate_node_form_data(definition, {})
    assert exc.value.enum is ErrorCode.TICKET_FORM_INVALID

    with pytest.raises(CustomError) as exc:
        engine.validate_node_form_data(definition, {"unknown": "value"})
    assert exc.value.enum is ErrorCode.TICKET_FORM_INVALID

    assert engine.validate_node_form_data(
        definition,
        {"review_note": "已核对"},
        {"risk": "low"},
    ) == {"review_note": "已核对", "risk": "low"}


PARALLEL = {
    "nodes": [
        {"id": "fork", "type": "parallel", "mode": "fork"},
        {"id": "b1", "type": "approve"},
        {"id": "b2", "type": "approve"},
        {"id": "merge", "type": "parallel", "mode": "merge",
         "fork_id": "fork", "strategy": "all"},
    ],
    "edges": [
        {"source": "fork", "target": "b1"},
        {"source": "fork", "target": "b2"},
        {"source": "b1", "target": "merge"},
        {"source": "b2", "target": "merge"},
    ],
}


def test_fork_activates_all_branches_at_once():
    assert sorted(engine.next_node_ids(PARALLEL, "fork", NODE_STATUS_APPROVED, {})) == ["b1", "b2"]


def test_end_node_has_no_successors():
    assert engine.next_node_ids(LINEAR, "end_ok", NODE_STATUS_APPROVED, {}) == []


def test_unknown_node_id_returns_empty_not_raises():
    """流程版本被改坏时不能让引擎抛异常打断事务，交由挂起逻辑处理。"""
    assert engine.next_node_ids(LINEAR, "ghost", NODE_STATUS_APPROVED, {}) == []


# ---------- 推进事务行为 ----------

def test_load_flow_json_rejects_unloaded_lazy_relation():
    """未预取的 FK QuerySet 不能再静默退化成空流程。"""
    ticket = make_ticket()
    del ticket._flow_json
    ticket.flow_version = FakeQuery([])

    with pytest.raises(CustomError) as exc:
        engine.load_flow_json(ticket)

    assert exc.value.enum is ErrorCode.TICKET_FLOW_VALIDATE_FAILED


class _Recorder:
    """收集引擎写入的活动日志，断言「每次状态变更必有一条活动」。"""

    def __init__(self):
        self.entries = []

    async def __call__(self, ticket, action, **kwargs):
        self.entries.append((action, kwargs))


@pytest.mark.asyncio
async def test_apply_decision_persists_approval_form_data(monkeypatch):
    ticket = make_ticket()
    node = make_node(
        node_id="ap1",
        approve_type=APPROVE_TYPE_ANY,
        approver_ids=("a",),
    )
    recorder = _Recorder()
    monkeypatch.setattr(engine, "record_activity", recorder)

    outcome = await engine._apply_decision(
        ticket,
        node,
        "a",
        "approve",
        None,
        form_data={"review_note": "已核对"},
    )

    assert outcome == NODE_STATUS_APPROVED
    assert node.form_data == {"review_note": "已核对"}
    assert "form_data" in node.saved[-1]


@pytest.mark.asyncio
async def test_advance_rejects_terminal_ticket(monkeypatch):
    """SPEC §4.2：所有推进入口先校验工单非终态。
    stale 支路越过汇聚回来时必须被丢弃，行锁防不住这种情况。"""
    ticket = make_ticket(status=TICKET_STATUS_COMPLETED)
    _install_engine_fakes(monkeypatch, ticket, nodes=[])

    handled = await engine.advance_ticket(
        ticket.id, ticket.tenant_id, trigger="operate"
    )
    assert handled is False
    assert ticket.saved == []


@pytest.mark.asyncio
async def test_advance_loads_pinned_version_without_flow_json_backdoor(monkeypatch):
    """推进必须显式查询钉住版本，并用它激活 start 后的首批节点。"""
    from types import SimpleNamespace

    ticket = make_ticket()
    del ticket._flow_json
    version = SimpleNamespace(id=ticket.flow_version_id, flow_json=LINEAR)
    recorder = _install_engine_fakes(
        monkeypatch, ticket, nodes=[], flow_version=version,
    )

    handled = await engine.advance_ticket(
        ticket.id, ticket.tenant_id, trigger="submit",
    )

    assert handled is True
    assert recorder.version_filters == [
        {"id": ticket.flow_version_id, "deleted": False},
    ]
    assert [row["node_id"] for row in recorder.created] == ["ap1"]


@pytest.mark.asyncio
async def test_advance_rejects_suspended_ticket_for_normal_trigger(monkeypatch):
    """6异常挂起态下普通推进要拒绝，仅管理员介入可恢复。"""
    ticket = make_ticket(status=TICKET_STATUS_SUSPENDED)
    _install_engine_fakes(monkeypatch, ticket, nodes=[])
    assert await engine.advance_ticket(ticket.id, ticket.tenant_id, "operate") is False


@pytest.mark.asyncio
async def test_node_reaching_abnormal_suspends_ticket(monkeypatch):
    """不变量：任一节点 5异常 ⟺ 工单 6异常挂起。"""
    ticket = make_ticket()
    node = make_node(node_id="ap1", status=NODE_STATUS_PENDING, approver_ids=("a",))
    recorder = _install_engine_fakes(monkeypatch, ticket, nodes=[node])

    await engine.mark_node_abnormal(node, ticket, reason="动态审批人解析失败")

    assert node.status == NODE_STATUS_ABNORMAL
    assert ticket.status == TICKET_STATUS_SUSPENDED
    assert any(action == "exception" for action, _ in recorder.entries)


@pytest.mark.asyncio
async def test_activate_approve_node_enters_pending(monkeypatch):
    ticket = make_ticket()
    created = _install_engine_fakes(monkeypatch, ticket, nodes=[]).created
    await engine.activate_nodes(ticket, ["ap1"], LINEAR)
    assert created[0]["status"] == NODE_STATUS_PENDING
    assert created[0]["entered_at"] is not None


@pytest.mark.asyncio
async def test_activate_auto_node_enters_running_directly(monkeypatch):
    """SPEC §4.8：自动节点激活时直接进 4执行中，不经 0待处理。"""
    flow = {
        "nodes": [{"id": "auto1", "type": "auto", "action": {"func": "noop"}}],
        "edges": [],
    }
    ticket = make_ticket()
    fakes = _install_engine_fakes(monkeypatch, ticket, nodes=[])
    await engine.activate_nodes(ticket, ["auto1"], flow)
    assert fakes.created[0]["status"] == NODE_STATUS_RUNNING


@pytest.mark.asyncio
async def test_dispatch_auto_node_saves_run_id_in_transaction(monkeypatch):
    from services.ticket import auto_action

    ticket = make_ticket()
    node = make_node(node_id="auto1", node_type="auto", status=NODE_STATUS_RUNNING)
    connection = object()

    async def _dispatch_action(_ticket, _action):
        return "run-1"

    monkeypatch.setattr(auto_action, "dispatch_action", _dispatch_action)

    await engine.dispatch_auto_node(
        ticket, node, {"action": {"func": "noop"}}, connection,
    )

    assert node.auto_run_id == "run-1"
    assert node.saved[-1] == ("auto_run_id", "utime")
    assert node.saved_using_db[-1] is connection


@pytest.mark.asyncio
async def test_resolve_node_approvers_uses_transaction_connection(monkeypatch):
    import models

    ticket = make_ticket()
    node = make_node(node_id="ap1", status=NODE_STATUS_PENDING)
    account = make_account("a")
    query = FakeQuery([account])
    connection = object()

    class _FakeAccount:
        @staticmethod
        def filter(**_kwargs):
            return query

    async def _record_activity(*_args, **_kwargs):
        return None

    monkeypatch.setattr(models, "Account", _FakeAccount)
    monkeypatch.setattr(engine, "record_activity", _record_activity)

    await engine.resolve_node_approvers(
        ticket, node, {"approvers": ["a"]}, connection,
    )

    assert query.using_db_calls == [connection]
    assert node.approvers.added == [account]
    assert node.approvers.added_using_db == [connection]


@pytest.mark.asyncio
async def test_change_node_approvers_uses_transaction_connection(monkeypatch):
    import models

    ticket = make_ticket()
    node = make_node(
        node_id="ap1", status=NODE_STATUS_PENDING, approver_ids=("a",),
        results={"a": {"result": "approve"}},
    )
    account_b = make_account("b")
    connection = object()

    class _FakeAccount:
        @staticmethod
        def filter(**_kwargs):
            return FakeQuery([account_b])

    async def _validate(*_args, **_kwargs):
        return ["b"]

    async def _record_activity(*_args, **_kwargs):
        return None

    monkeypatch.setattr(models, "Account", _FakeAccount)
    monkeypatch.setattr(engine.validators, "validate_approver_change", _validate)
    monkeypatch.setattr(engine, "record_activity", _record_activity)

    await engine.change_node_approvers(
        ticket, node, "transfer", ["b"], actor_id="a", connection=connection,
    )

    assert node.approvers.removed_using_db == [connection]
    assert node.approvers.added_using_db == [connection]
    assert node.saved_using_db[-1] is connection


@pytest.mark.asyncio
async def test_every_status_change_writes_one_activity(monkeypatch):
    """不变量：每次工单/节点 status 变更必有一条对应 ticket_activity。"""
    ticket = make_ticket()
    node = make_node(node_id="ap1", approve_type=APPROVE_TYPE_ANY, approver_ids=("a",))
    recorder = _install_engine_fakes(monkeypatch, ticket, nodes=[node])

    await engine.advance_ticket(
        ticket.id, ticket.tenant_id, trigger="operate",
        actor_id="a", node_instance_id=node.id, decision="approve",
    )
    assert len(recorder.entries) >= 1
    assert node.status == NODE_STATUS_APPROVED


@pytest.mark.asyncio
async def test_duplicate_approve_is_idempotent(monkeypatch):
    """SPEC §4.9：重复 approve 幂等成功，results 不重复记录。"""
    ticket = make_ticket()
    node = make_node(
        node_id="ap1", approve_type=APPROVE_TYPE_ALL, approver_ids=("a", "b"),
        results={"a": {"result": "approve"}},
    )
    _install_engine_fakes(monkeypatch, ticket, nodes=[node])

    await engine.advance_ticket(
        ticket.id, ticket.tenant_id, "operate",
        actor_id="a", node_instance_id=node.id, decision="approve",
    )
    assert len(node.results) == 1
    assert node.status == NODE_STATUS_PENDING   # 会签仍缺 b


@pytest.mark.asyncio
async def test_cancelled_branch_node_rejects_operation(monkeypatch):
    """任一汇聚取消的落败支路仍是待处理态，但引擎不得再接受操作。"""
    ticket = make_ticket()
    node = make_node(
        node_id="b2", approver_ids=("a",),
        results={RESULT_KEY_CANCELLED: True},
    )
    _install_engine_fakes(monkeypatch, ticket, nodes=[node])

    handled = await engine.advance_ticket(
        ticket.id, ticket.tenant_id, "operate",
        actor_id="a", node_instance_id=node.id, decision="approve",
    )

    assert handled is False
    assert node.status == NODE_STATUS_PENDING
    assert ticket.status == TICKET_STATUS_PROCESSING


@pytest.mark.asyncio
async def test_filtered_duplicate_merge_target_does_not_suspend(monkeypatch):
    """原本有后继但被汇聚 CAS 过滤时是重复推进，应静默成功。"""
    from services.ticket import merge

    ticket = make_ticket()
    node = make_node(node_id="b1", approver_ids=("a",))
    _install_engine_fakes(monkeypatch, ticket, nodes=[node], flow=PARALLEL)

    async def _filter_all(*_args, **_kwargs):
        return []

    monkeypatch.setattr(merge, "filter_merge_ready", _filter_all)

    handled = await engine.advance_ticket(
        ticket.id, ticket.tenant_id, "operate",
        actor_id="a", node_instance_id=node.id, decision="approve",
    )

    assert handled is True
    assert node.status == NODE_STATUS_APPROVED
    assert ticket.status == TICKET_STATUS_PROCESSING


@pytest.mark.asyncio
async def test_reaching_end_node_closes_ticket(monkeypatch):
    ticket = make_ticket()
    node = make_node(node_id="ap1", approve_type=APPROVE_TYPE_ANY, approver_ids=("a",))
    _install_engine_fakes(monkeypatch, ticket, nodes=[node], flow=LINEAR)

    await engine.advance_ticket(
        ticket.id, ticket.tenant_id, "operate",
        actor_id="a", node_instance_id=node.id, decision="approve",
    )
    assert ticket.status == TICKET_STATUS_COMPLETED
    assert ticket.closed_at is not None


def _install_engine_fakes(monkeypatch, ticket, nodes, flow=None, flow_version=None):
    """把 engine 模块里的模型引用、事务、活动记录全换成假对象。

    in_transaction 换成空上下文——真实行锁语义在 UAT 验证（SPEC §9.2），
    单测只验证「调用了 select_for_update」与业务分支正确性。
    """
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from tests.ticket.conftest import FakeQuery

    recorder = _Recorder()
    created = []
    version_filters = []

    class _FakeTicket:
        @staticmethod
        def filter(**kwargs):
            match = (
                kwargs.get("id") in (None, ticket.id)
                and kwargs.get("tenant_id") in (None, ticket.tenant_id)
            )
            return FakeQuery([ticket] if match else [])

    class _FakeNodeInstance:
        @staticmethod
        def filter(**kwargs):
            rows = nodes
            if "id" in kwargs:
                rows = [n for n in rows if n.id == kwargs["id"]]
            if "status" in kwargs:
                rows = [n for n in rows if n.status == kwargs["status"]]
            if "status__in" in kwargs:
                rows = [n for n in rows if n.status in kwargs["status__in"]]
            return FakeQuery(rows)

        @staticmethod
        async def create(**kwargs):
            created.append(kwargs)
            node = SimpleNamespace(id=f"inst-{len(created)}", **kwargs)
            node.approvers = __import__(
                "tests.ticket.conftest", fromlist=["FakeM2M"]
            ).FakeM2M()
            nodes.append(node)
            return node

    class _FakeFlowVersion:
        @staticmethod
        def filter(**kwargs):
            version_filters.append(kwargs)
            matched = flow_version is not None and kwargs.get("id") == flow_version.id
            return FakeQuery([flow_version] if matched else [])

    class _FakeTicketType:
        @staticmethod
        def filter(**kwargs):
            matched = (
                kwargs.get("id") == ticket.ticket_type_id
                and kwargs.get("tenant_id") == ticket.tenant_id
                and kwargs.get("deleted") is False
            )
            ticket_type = SimpleNamespace(id=ticket.ticket_type_id, sla_config={})
            return FakeQuery([ticket_type] if matched else [])

    @asynccontextmanager
    async def _fake_tx():
        yield None

    monkeypatch.setattr(engine, "Ticket", _FakeTicket)
    monkeypatch.setattr(engine, "TicketNodeInstance", _FakeNodeInstance)
    monkeypatch.setattr(engine, "TicketFlowVersion", _FakeFlowVersion)
    monkeypatch.setattr(engine, "TicketType", _FakeTicketType)
    monkeypatch.setattr(engine, "in_transaction", _fake_tx)
    monkeypatch.setattr(engine, "record_activity", recorder)
    if flow_version is None:
        monkeypatch.setattr(
            engine, "load_flow_json", lambda _ticket, version=None: flow or LINEAR,
        )
    monkeypatch.setattr(engine, "dispatch_auto_node", lambda *a, **k: None)

    recorder.created = created
    recorder.version_filters = version_filters
    return recorder
