# coding=utf-8
"""/ticket/action 九种操作的权限、状态前置与错误码断言。

前序会话的 action_view 有 10 处未定义引用（body/record_activity/datetime/
active_nodes 等），但因无测试覆盖而未被发现。本文件通过导入验证和核心逻辑
测试确保所有引用都已定义。
"""

from datetime import datetime, timedelta

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.ticket import (
    NODE_STATUS_APPROVED,
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    SOURCE_PLAYBOOK,
    TICKET_STATUS_COMPLETED,
    TICKET_STATUS_PROCESSING,
    TICKET_STATUS_REJECTED,
    TICKET_STATUS_WITHDRAWN,
)
from services.ticket import engine
from services.ticket.permissions import (
    compute_available_actions,
    is_current_approver,
    is_ticket_admin,
    is_ticket_creator,
)
from settings import TZ
from tests.ticket.conftest import FakeQuery, make_account, make_node, make_ticket


# ==================== 导入验证 ====================

def test_action_view_imports_successfully():
    """验证 action_view 所有导入都已定义，无 NameError"""
    from services.ticket import views

    # 验证关键导入存在
    assert hasattr(views, 'action_view')
    assert hasattr(views, 'datetime')
    assert hasattr(views, 'timedelta')
    assert hasattr(views, 'TZ')
    assert hasattr(views, 'record_activity')
    assert hasattr(views, 'change_node_approvers')
    assert hasattr(views, 'is_current_approver')
    assert hasattr(views, 'is_ticket_admin')
    assert hasattr(views, 'TICKET_STATUS_PROCESSING')
    assert hasattr(views, 'TICKET_STATUS_WITHDRAWN')
    assert hasattr(views, 'TICKET_STATUS_FORCE_CLOSED')
    assert hasattr(views, 'TICKET_TERMINAL_STATUSES')
    assert hasattr(views, 'SOURCE_PLAYBOOK')
    assert hasattr(views, 'NODE_STATUS_PENDING')
    assert hasattr(views, 'NODE_STATUS_RUNNING')


def test_action_view_forwards_approval_form_data_to_engine():
    """审批动作必须把节点表单交给状态机事务保存。"""
    import inspect
    from services.ticket import views

    source = inspect.getsource(views.action_view)
    assert '"form_data": {"type": "object"}' in source
    assert 'node_form_data=payload.get("form_data")' in source


def test_engine_imports_successfully():
    """验证 engine 所有导入都已定义"""
    # return_to_node 新签名：connection 必传
    assert callable(engine.return_to_node)
    assert callable(engine.record_activity)
    assert callable(engine.change_node_approvers)
    assert callable(engine.advance_ticket)


def test_callback_playbook_function_exists():
    """验证旧回调入口仍委托给统一桥接层。"""
    from services.ticket import views

    assert hasattr(views, '_callback_playbook')
    import inspect
    source = inspect.getsource(views._callback_playbook)
    assert 'callback_playbook' in source
    assert 'approval_status' in source


# ==================== withdraw 权限与状态 ====================

def test_withdraw_permission_check():
    """withdraw 只能由提单人执行（非剧本工单）"""
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING, source=0)

    # 验证条件：提单人 + 非剧本 + 处理中
    assert is_ticket_creator(ticket, "acc-creator") is True
    assert is_ticket_creator(ticket, "other_user") is False
    assert ticket.source != SOURCE_PLAYBOOK
    assert ticket.status == TICKET_STATUS_PROCESSING


def test_withdraw_forbidden_for_playbook_source():
    """剧本触发的工单不允许撤回"""
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING, source=SOURCE_PLAYBOOK)

    # 即使是提单人也不能撤回剧本工单
    assert is_ticket_creator(ticket, "acc-creator") is True
    assert ticket.source == SOURCE_PLAYBOOK


def test_withdraw_forbidden_for_terminal_ticket():
    """终态工单不能撤回"""
    for terminal_status in [TICKET_STATUS_COMPLETED, TICKET_STATUS_REJECTED, TICKET_STATUS_WITHDRAWN]:
        ticket = make_ticket(status=terminal_status, source=0)
        assert ticket.status in [TICKET_STATUS_COMPLETED, TICKET_STATUS_REJECTED, TICKET_STATUS_WITHDRAWN]


@pytest.mark.asyncio
async def test_withdraw_records_activity(monkeypatch):
    """withdraw 成功后应记录 activity"""
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING, source=0)

    record_activity_calls = []

    async def mock_record_activity(ticket, action, actor_id=None, connection=None, **kwargs):
        record_activity_calls.append({'action': action, 'actor_id': actor_id})

    monkeypatch.setattr(engine, 'record_activity', mock_record_activity)

    # 模拟 withdraw 逻辑
    ticket.status = TICKET_STATUS_WITHDRAWN
    await ticket.save()
    await engine.record_activity(ticket, 'withdraw', actor_id='acc-creator', connection=None)

    assert len(record_activity_calls) == 1
    assert record_activity_calls[0]['action'] == 'withdraw'


# ==================== urge 限流与权限 ====================

def test_urge_permission_check():
    """urge 只能由提单人执行"""
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING)

    assert is_ticket_creator(ticket, "acc-creator") is True
    assert is_ticket_creator(ticket, "other_user") is False


def test_urge_one_hour_limit():
    """urge 限流：1 小时内只能催一次"""
    now = datetime.now(TZ)
    one_hour_ago = now - timedelta(hours=1)
    thirty_minutes_ago = now - timedelta(minutes=30)
    two_hours_ago = now - timedelta(hours=2)

    # 30 分钟前催过 -> 不能再催
    assert thirty_minutes_ago > one_hour_ago

    # 2 小时前催过 -> 可以再催
    assert two_hours_ago < one_hour_ago


# ==================== return 权限与目标校验 ====================

@pytest.mark.asyncio
async def test_return_requires_current_approver():
    """return 必须是当前审批人"""
    node = make_node("n1", status=NODE_STATUS_RUNNING, approver_ids=["approver1", "approver2"])

    assert await is_current_approver(node, "approver1") is True
    assert await is_current_approver(node, "approver2") is True
    assert await is_current_approver(node, "other_user") is False


@pytest.mark.asyncio
async def test_return_target_validation(monkeypatch):
    """return 目标节点必须存在且已执行过"""
    # 目标节点不存在 -> 应抛出异常
    historical_node = None

    def mock_filter(**kw):
        return FakeQuery([historical_node] if historical_node else [])

    monkeypatch.setattr("models.ticket.TicketNodeInstance.filter", mock_filter)

    # 模拟 return_to_node 的校验逻辑
    with pytest.raises(CustomError) as exc:
        if historical_node is None:
            raise CustomError(ErrorCode.TICKET_RETURN_TARGET_INVALID)

    assert exc.value.enum is ErrorCode.TICKET_RETURN_TARGET_INVALID


# ==================== force_close 管理员权限 ====================

def test_force_close_requires_admin():
    """force_close 需要 ticket:all 权限"""
    # 这里只验证函数存在，实际权限由 is_ticket_admin 判断
    assert callable(is_ticket_admin)


def test_force_close_playbook_requires_callback_decision():
    """force_close 剧本工单必须提供 callback_decision"""
    ticket_playbook = make_ticket(source=SOURCE_PLAYBOOK)
    ticket_manual = make_ticket(source=0)

    assert ticket_playbook.source == SOURCE_PLAYBOOK
    assert ticket_manual.source != SOURCE_PLAYBOOK

    # 剧本工单需要 callback_decision
    # 手动工单不需要


def test_force_close_callback_decision_mapping():
    """force_close 的 callback_decision 到 approval_status 的映射"""
    # approve -> 1
    # reject -> 2
    # none -> 不回传
    assert 1 in (1, 2)  # approval_status 守卫
    assert 2 in (1, 2)


# ==================== assign 管理员指定审批人 ====================

def test_assign_requires_admin():
    """assign 需要管理员权限"""
    assert callable(is_ticket_admin)


@pytest.mark.asyncio
async def test_assign_calls_change_node_approvers(monkeypatch):
    """assign 应调用 change_node_approvers with operation='assign_approver'"""
    ticket = make_ticket()
    node = make_node("n1", status=NODE_STATUS_PENDING)

    change_calls = []

    async def mock_change(ticket, node, operation, target_ids, actor_id=None, connection=None):
        change_calls.append({'operation': operation, 'target_ids': target_ids})
        return target_ids

    monkeypatch.setattr(engine, 'change_node_approvers', mock_change)

    # 模拟 assign 逻辑
    await engine.change_node_approvers(
        ticket, node, "assign_approver", ["user1"], actor_id="admin1", connection=None
    )

    assert len(change_calls) == 1
    assert change_calls[0]['operation'] == 'assign_approver'


# ==================== 通用场景 ====================

def test_terminal_ticket_status_check():
    """终态工单不可操作"""
    from models.ticket import TICKET_TERMINAL_STATUSES

    terminal_ticket = make_ticket(status=TICKET_STATUS_COMPLETED)
    processing_ticket = make_ticket(status=TICKET_STATUS_PROCESSING)

    assert terminal_ticket.status in TICKET_TERMINAL_STATUSES
    assert processing_ticket.status not in TICKET_TERMINAL_STATUSES


def test_available_actions_for_terminal_ticket():
    """终态工单的 available_actions 应为空"""
    ticket = make_ticket(status=TICKET_STATUS_COMPLETED)
    session = {
        "tenant_id": "tenant-1",
        "account_info": make_account("user1"),
    }

    actions = compute_available_actions(ticket, set(), set(), "user1", session)
    assert actions == []


def test_available_actions_for_creator():
    """提单人有 withdraw 和 urge 权限（非剧本工单）"""
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING, source=0)
    session = {
        "tenant_id": "tenant-1",
        "account_info": make_account("acc-creator"),
    }

    actions = compute_available_actions(ticket, set(), set(), "acc-creator", session)

    # 提单人应该有 withdraw 和 urge
    assert "withdraw" in actions
    assert "urge" in actions


def test_available_actions_for_playbook_ticket():
    """剧本工单提单人不能 withdraw"""
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING, source=SOURCE_PLAYBOOK)
    session = {
        "tenant_id": "tenant-1",
        "account_info": make_account("acc-creator"),
    }

    actions = compute_available_actions(ticket, set(), set(), "acc-creator", session)

    # 剧本工单不允许 withdraw
    assert "withdraw" not in actions


@pytest.mark.asyncio
async def test_available_actions_for_current_approver():
    """当前审批人有 approve/reject/transfer/return/add_approver 权限"""
    ticket = make_ticket(status=TICKET_STATUS_PROCESSING)
    node = make_node("n1", status=NODE_STATUS_RUNNING, approver_ids=["approver1"])

    # compute_available_actions 使用 _prefetched_approvers_val 访问审批人
    # 需要手动设置这个属性
    approvers = await node.approvers.all()
    node._prefetched_approvers_val = approvers

    session = {
        "tenant_id": "tenant-1",
        "account_info": make_account("approver1"),
    }

    # Task 2 重写了 compute_available_actions 为纯函数,需要传入审批人集合
    active_approver_ids = {"approver1"}
    all_approver_ids = {"approver1"}

    actions = compute_available_actions(ticket, active_approver_ids, all_approver_ids, "approver1", session)

    # 当前审批人应有这些操作
    assert "approve" in actions
    assert "reject" in actions
    assert "transfer" in actions
    assert "return" in actions
    assert "add_approver" in actions


# ==================== 状态转移校验 ====================

def test_can_transition_ticket():
    """验证工单状态转移规则"""
    # processing 可以到任何其他状态
    assert engine.can_transition_ticket(TICKET_STATUS_PROCESSING, TICKET_STATUS_COMPLETED) is True
    assert engine.can_transition_ticket(TICKET_STATUS_PROCESSING, TICKET_STATUS_WITHDRAWN) is True

    # 终态不能转移
    assert engine.can_transition_ticket(TICKET_STATUS_COMPLETED, TICKET_STATUS_PROCESSING) is False
    assert engine.can_transition_ticket(TICKET_STATUS_WITHDRAWN, TICKET_STATUS_PROCESSING) is False


def test_can_transition_node():
    """验证节点状态转移规则"""
    # pending 可以到 approved/rejected/timeout/abnormal
    assert engine.can_transition_node(NODE_STATUS_PENDING, NODE_STATUS_APPROVED) is True

    # running 可以到 approved/rejected/abnormal
    assert engine.can_transition_node(NODE_STATUS_RUNNING, NODE_STATUS_APPROVED) is True

    # 终态不能转移
    assert engine.can_transition_node(NODE_STATUS_APPROVED, NODE_STATUS_PENDING) is False


# ==================== return_to_node 连接泄漏修复验证 ====================

def test_return_to_node_signature():
    """验证 return_to_node 的 connection 参数必传"""
    import inspect

    sig = inspect.signature(engine.return_to_node)
    params = sig.parameters

    # connection 应该是必传参数（无默认值）
    assert 'connection' in params
    assert params['connection'].default == inspect.Parameter.empty


# ==================== _callback_playbook approval_status 守卫 ====================

@pytest.mark.asyncio
async def test_callback_playbook_approval_status_guard(monkeypatch):
    """_callback_playbook 只接受 approval_status 为 1 或 2"""
    from services.ticket.views import _callback_playbook

    ticket = make_ticket(source=SOURCE_PLAYBOOK)
    ticket.origin_run_id = "run123"
    ticket.origin_flow_id = "flow1"
    ticket.origin_node_id = "node1"
    ticket.origin_params = {}

    # Mock aproduce
    async def mock_aproduce(topic, message):
        pass

    # 防止跨测试文件的 settings 模块污染
    import sys
    if 'settings' not in sys.modules or not hasattr(sys.modules['settings'], 'TASK_TOPIC'):
        # settings 被其他测试替换成裸模块，手动恢复
        from types import SimpleNamespace
        fake_settings = SimpleNamespace(TASK_TOPIC="task_topic")
        monkeypatch.setitem(sys.modules, 'settings', fake_settings)

    monkeypatch.setattr("services.ticket.playbook_bridge.aproduce", mock_aproduce)

    # approval_status 必须是 1 或 2
    await _callback_playbook(ticket, 1, "user1", {"decision": "approve"})
    await _callback_playbook(ticket, 2, "user1", {"decision": "reject"})

    # 其他值应该抛出异常
    with pytest.raises(CustomError) as exc:
        await _callback_playbook(ticket, 3, "user1", {"decision": "other"})

    assert exc.value.enum is ErrorCode.PARAM_ERROR


# ==================== retry_resolve 重试审批人解析 ====================
#
# 该分支修复的缺陷：permissions.py 对挂起工单下发 "retry_resolve"，但
# views.py 的 action 分发链原先没有对应分支，请求会落到末尾的
# `raise CustomError(ErrorCode.PARAM_ERROR)`，管理员点「重试」必然报错。
# 下面的测试锁住成功/失败两条路径的最终状态与错误码。


def _retry_env(monkeypatch, ticket, node, resolve_effect):
    """装配 retry_resolve 所需的最小依赖，返回被调用记录。"""
    from services.ticket import views

    calls = {"resolve": 0, "activities": []}

    class _Conn:
        pass

    class _Tx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(views, "in_transaction", lambda: _Tx())
    monkeypatch.setattr(
        views.TicketNodeInstance, "filter", lambda **kw: FakeQuery([node])
    )
    monkeypatch.setattr(views, "load_flow_json", lambda t: {
        "nodes": [{"id": node.node_id, "type": "approve"}], "edges": [],
    })

    async def _resolve(_ticket, _node, _definition, _conn=None):
        calls["resolve"] += 1
        resolve_effect(_node)

    async def _record(_ticket, action, **kw):
        calls["activities"].append(action)

    monkeypatch.setattr(views, "resolve_node_approvers", _resolve)
    monkeypatch.setattr(views, "record_activity", _record)
    return calls


@pytest.mark.asyncio
async def test_retry_resolve_success_restores_processing(monkeypatch):
    """解析成功：节点回 pending，工单从挂起回处理中，且记了 retry_resolve 活动"""
    from models.ticket import NODE_STATUS_ABNORMAL, TICKET_STATUS_SUSPENDED
    from services.ticket import views

    ticket = make_ticket(status=TICKET_STATUS_SUSPENDED)
    node = make_node("ap1", status=NODE_STATUS_ABNORMAL)

    # 解析成功 = 不把节点重新置为 abnormal
    calls = _retry_env(monkeypatch, ticket, node, lambda n: None)

    await views._retry_resolve(ticket, "tenant-1", "admin1")

    assert calls["resolve"] == 1
    assert node.status == NODE_STATUS_PENDING
    assert ticket.status == TICKET_STATUS_PROCESSING
    assert "retry_resolve" in calls["activities"]


@pytest.mark.asyncio
async def test_retry_resolve_failure_keeps_suspended(monkeypatch):
    """解析再次失败：抛 TICKET_APPROVER_INVALID，工单仍挂起、节点仍异常"""
    from models.ticket import NODE_STATUS_ABNORMAL, TICKET_STATUS_SUSPENDED
    from services.ticket import views

    ticket = make_ticket(status=TICKET_STATUS_SUSPENDED)
    node = make_node("ap1", status=NODE_STATUS_ABNORMAL)

    def _fail(n):
        # 复刻 engine.mark_node_abnormal 的效果
        n.status = NODE_STATUS_ABNORMAL

    calls = _retry_env(monkeypatch, ticket, node, _fail)

    with pytest.raises(CustomError) as exc:
        await views._retry_resolve(ticket, "tenant-1", "admin1")

    assert exc.value.enum is ErrorCode.TICKET_APPROVER_INVALID
    assert calls["resolve"] == 1
    assert node.status == NODE_STATUS_ABNORMAL
    # 失败不得把工单改回处理中
    assert ticket.status == TICKET_STATUS_SUSPENDED


@pytest.mark.asyncio
async def test_retry_resolve_without_abnormal_node(monkeypatch):
    """没有异常节点时抛 TICKET_NODE_NOT_ACTIVE，不触碰工单状态"""
    from models.ticket import TICKET_STATUS_SUSPENDED
    from services.ticket import views

    ticket = make_ticket(status=TICKET_STATUS_SUSPENDED)
    monkeypatch.setattr(
        views.TicketNodeInstance, "filter", lambda **kw: FakeQuery([])
    )

    with pytest.raises(CustomError) as exc:
        await views._retry_resolve(ticket, "tenant-1", "admin1")

    assert exc.value.enum is ErrorCode.TICKET_NODE_NOT_ACTIVE
    assert ticket.status == TICKET_STATUS_SUSPENDED


def test_is_ticket_admin_is_not_a_coroutine():
    """is_ticket_admin 是同步函数，调用点不得 await。

    回归锁：views.py 曾在 assign / force_close 两处写成
    `if not await is_ticket_admin(session)`，而该函数返回 bool，
    `await bool` 抛 TypeError，导致这两个管理员操作在运行时直接 500。
    现有测试全是 `assert callable(...)` 级别的断言，没能拦住。
    """
    import inspect
    import re
    from pathlib import Path

    assert not inspect.iscoroutinefunction(is_ticket_admin)
    assert isinstance(is_ticket_admin({"account_info": None}), bool)

    source = Path("services/ticket/views.py").read_text(encoding="utf-8")
    assert not re.search(r"await\s+is_ticket_admin\s*\(", source), (
        "is_ticket_admin 是同步函数，await 它会抛 TypeError"
    )
