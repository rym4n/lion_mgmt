# coding=utf-8
"""SLA 计算与调度。计算是纯函数，优先锁死公式；
claim/lease 的真互斥靠 UAT（SPEC §9.2），单测验证 claim 的判定逻辑。"""

from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from models.ticket import TICKET_STATUS_SUSPENDED
from services.ticket import sla_scheduler as sla
from tests.ticket.conftest import make_node, make_ticket

CTIME = datetime(2026, 8, 10, 9, 0, 0)
CONFIG = {"respond": 900, "resolve": 7200, "warn_before": 600}


def test_respond_and_resolve_anchored_on_ctime():
    """公式以 ctime 为锚点，不是「改动时刻」（SPEC §4.7）。"""
    respond, resolve = sla.calculate_sla(CTIME, CONFIG)
    assert respond == CTIME + timedelta(seconds=900)
    assert resolve == CTIME + timedelta(seconds=7200)


def test_partial_config_leaves_missing_side_null():
    respond, resolve = sla.calculate_sla(CTIME, {"respond": 3600})
    assert respond == CTIME + timedelta(seconds=3600)
    assert resolve is None


def test_empty_config_yields_no_sla():
    assert sla.calculate_sla(CTIME, {}) == (None, None)
    assert sla.calculate_sla(CTIME, None) == (None, None)


def test_playbook_flow_deadline_caps_resolve():
    """SPEC §2.2：剧本触发的工单过期时间取 min(节点/工单 SLA, 剧本剩余 flow_timeout)。"""
    flow_deadline = CTIME + timedelta(seconds=300)
    _, resolve = sla.calculate_sla(CTIME, CONFIG, flow_deadline=flow_deadline)
    assert resolve == flow_deadline


def test_flow_deadline_ignored_when_later_than_sla():
    flow_deadline = CTIME + timedelta(seconds=99999)
    _, resolve = sla.calculate_sla(CTIME, CONFIG, flow_deadline=flow_deadline)
    assert resolve == CTIME + timedelta(seconds=7200)


def test_flow_deadline_applies_even_without_sla_config():
    """无 SLA 配置但有剧本超时时，仍受剧本超时约束，否则工单永不终结。"""
    flow_deadline = CTIME + timedelta(seconds=300)
    _, resolve = sla.calculate_sla(CTIME, {}, flow_deadline=flow_deadline)
    assert resolve == flow_deadline


# ---------- 挂起期间 SLA 暂停 ----------

def test_suspension_extends_deadline_by_paused_duration():
    """SPEC §4.7：挂起时长从 deadline 中扣除，恢复时顺延。"""
    deadline = CTIME + timedelta(seconds=3600)
    shifted = sla.shift_for_suspension(
        deadline,
        suspended_at=CTIME + timedelta(seconds=600),
        resumed_at=CTIME + timedelta(seconds=1200),
    )
    assert shifted == deadline + timedelta(seconds=600)


def test_suspension_shift_noop_without_deadline_or_timestamps():
    assert sla.shift_for_suspension(None, CTIME, CTIME) is None
    deadline = CTIME + timedelta(seconds=60)
    assert sla.shift_for_suspension(deadline, None, CTIME) == deadline


# ---------- 预警提前量 ----------

def test_warn_fires_once_when_crossing_window():
    """预警在 deadline - warn_before 落入 [上次扫描, 本次扫描) 时触发一次。"""
    deadline = CTIME + timedelta(seconds=1200)
    warn_at = deadline - timedelta(seconds=600)   # CTIME+600
    assert sla.should_warn(deadline, 600, last_sweep=warn_at - timedelta(seconds=30),
                           now=warn_at + timedelta(seconds=1)) is True


def test_warn_does_not_fire_before_window():
    deadline = CTIME + timedelta(seconds=1200)
    assert sla.should_warn(deadline, 600, last_sweep=CTIME,
                           now=CTIME + timedelta(seconds=100)) is False


def test_warn_does_not_refire_after_window_passed():
    """窗口已过（上次扫描就在预警点之后）不再重复发。"""
    deadline = CTIME + timedelta(seconds=1200)
    assert sla.should_warn(deadline, 600,
                           last_sweep=CTIME + timedelta(seconds=700),
                           now=CTIME + timedelta(seconds=800)) is False


def test_warn_default_is_900_seconds():
    """SPEC §4.7：warn_before 默认 900 秒，respond 与 resolve 共用。"""
    assert sla.DEFAULT_WARN_BEFORE_SECONDS == 900


# ---------- claim / lease 互斥 ----------

@pytest.mark.asyncio
async def test_claim_succeeds_on_unclaimed_row():
    ticket = make_ticket()
    assert await sla.claim_row(ticket, owner="worker-1", now=CTIME) is True
    assert ticket.sweep_owner == "worker-1"
    assert ticket.sweep_expires_at == CTIME + timedelta(seconds=sla.CLAIM_TTL_SECONDS)


@pytest.mark.asyncio
async def test_claim_ttl_matches_command_center_pattern():
    """复用既有范式的 300 秒租约，避免两套超时语义。"""
    assert sla.CLAIM_TTL_SECONDS == 300


@pytest.mark.asyncio
async def test_claim_fails_while_another_worker_holds_valid_lease():
    ticket = make_ticket()
    ticket.sweep_owner = "worker-1"
    ticket.sweep_expires_at = CTIME + timedelta(seconds=120)
    assert await sla.claim_row(ticket, owner="worker-2", now=CTIME) is False
    assert ticket.sweep_owner == "worker-1"


@pytest.mark.asyncio
async def test_claim_handles_aware_db_lease_from_another_worker():
    """TIMESTAMPTZ 租约与默认当前时间比较时必须同为 aware。"""
    ticket = make_ticket()
    ticket.sweep_owner = "worker-1"
    ticket.sweep_expires_at = datetime.now(sla.TZ) + timedelta(seconds=120)

    assert await sla.claim_row(ticket, owner="worker-2") is False
    assert ticket.sweep_owner == "worker-1"


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_by_other_worker():
    """租约到期而未完成的行被重新 claim，因此不需要看门狗续约。"""
    ticket = make_ticket()
    ticket.sweep_owner = "worker-1"
    ticket.sweep_expires_at = CTIME - timedelta(seconds=1)
    assert await sla.claim_row(ticket, owner="worker-2", now=CTIME) is True
    assert ticket.sweep_owner == "worker-2"


@pytest.mark.asyncio
async def test_owner_can_reenter_its_own_claim():
    ticket = make_ticket()
    ticket.sweep_owner = "worker-1"
    ticket.sweep_expires_at = CTIME + timedelta(seconds=120)
    assert await sla.claim_row(ticket, owner="worker-1", now=CTIME) is True


# ---------- 节点超时策略 ----------

@pytest.mark.parametrize(
    "policy,expected",
    [
        ("auto_approve", 1),      # NODE_STATUS_APPROVED
        ("auto_reject", 2),       # NODE_STATUS_REJECTED
        ("auto_fail", 3),         # NODE_STATUS_TIMEOUT
        ("notify_only", None),    # 只发通知，不改节点状态
    ],
)
def test_timeout_policy_maps_to_node_outcome(policy, expected):
    assert sla.timeout_outcome(policy) == expected


def test_transfer_policy_keeps_node_pending():
    """转派给指定人：节点仍待处理，只换审批人。"""
    assert sla.timeout_outcome("transfer") is None


def test_unknown_policy_defaults_to_notify_only():
    """未知策略不得默默通过——静默通过审批节点是安全漏洞。"""
    assert sla.timeout_outcome("garbage") is None


def test_notify_only_requires_ticket_resolve_deadline():
    """SPEC §4.6：「仅通知」必须依赖工单级 sla_resolve_at 兜底终结，
    未配 resolve 时限则禁止选它（发布校验拦截）。"""
    assert sla.notify_only_allowed({"respond": 900, "resolve": 7200}) is True
    assert sla.notify_only_allowed({"respond": 900}) is False
    assert sla.notify_only_allowed({}) is False


@pytest.mark.asyncio
async def test_timeout_notification_is_idempotent():
    """SPEC §4.6：同一节点的超时通知只发一次，靠活动表已有
    sla_escalate 记录判断（60 秒一轮扫描会反复看到同一节点）。"""
    node = make_node(node_id="ap1", deadline=CTIME - timedelta(seconds=1))
    already = await sla.already_escalated(
        node, activities_provider=lambda: [
            {"node_instance_id": node.id, "action": "sla_escalate"}
        ]
    )
    assert already is True
    fresh = await sla.already_escalated(node, activities_provider=lambda: [])
    assert fresh is False


@pytest.mark.asyncio
async def test_sweep_warns_active_node_only_once(monkeypatch):
    """预警必须由 sweep 接线，并靠 sla_warn 活动在重复扫描时判重。"""
    from services.ticket import engine
    from tests.ticket.conftest import FakeQuery

    ticket = make_ticket()
    node = make_node(
        node_id="ap1",
        deadline=CTIME + timedelta(seconds=600),
    )
    activities = []
    node_filters = []

    class _NodeModel:
        @staticmethod
        def filter(**kwargs):
            node_filters.append(kwargs)
            if "deadline__gt" in kwargs:
                return FakeQuery([node])
            return FakeQuery([])

    class _TicketModel:
        @staticmethod
        def filter(*_args, **kwargs):
            if kwargs.get("id") == ticket.id:
                return FakeQuery([ticket])
            return FakeQuery([])

    class _TicketTypeModel:
        @staticmethod
        def filter(**_kwargs):
            return FakeQuery([SimpleNamespace(sla_config={"warn_before": 600})])

    class _ActivityModel:
        @staticmethod
        def filter(**kwargs):
            rows = [
                activity for activity in activities
                if activity["ticket_id"] == kwargs.get("ticket_id")
                and activity["node_instance_id"] == kwargs.get("node_instance_id")
                and activity["action"] == kwargs.get("action")
            ]
            return FakeQuery(rows)

    @asynccontextmanager
    async def _fake_tx():
        yield object()

    async def _record(ticket_obj, action, node_instance=None, payload=None, **_kwargs):
        activities.append({
            "ticket_id": ticket_obj.id,
            "node_instance_id": getattr(node_instance, "id", None),
            "action": action,
            "payload": payload or {},
        })

    monkeypatch.setattr(sla, "TicketNodeInstance", _NodeModel)
    monkeypatch.setattr(sla, "Ticket", _TicketModel)
    monkeypatch.setattr(sla, "TicketType", _TicketTypeModel)
    monkeypatch.setattr(sla, "TicketActivity", _ActivityModel)
    monkeypatch.setattr(sla, "in_transaction", _fake_tx)
    monkeypatch.setattr(engine, "record_activity", _record)

    first = await sla.sweep_once("worker-1", now=CTIME, last_sweep=None)
    second = await sla.sweep_once("worker-1", now=CTIME, last_sweep=None)

    assert first["warned"] == 1
    assert second["warned"] == 0
    assert [activity["action"] for activity in activities] == ["sla_warn"]
    assert activities[0]["payload"]["warn_before"] == 600
    assert any(
        call.get("status__in") == [sla.NODE_STATUS_PENDING, sla.NODE_STATUS_RUNNING]
        and "deadline__lte" in call
        for call in node_filters
    )


@pytest.mark.asyncio
async def test_sweep_continues_after_one_row_fails(monkeypatch):
    """单行异常只记录日志，后续行仍继续处理。"""
    from tests.ticket.conftest import FakeQuery

    failed_node = make_node(node_id="auto-failed", status=sla.NODE_STATUS_RUNNING)
    healthy_node = make_node(node_id="auto-healthy", status=sla.NODE_STATUS_RUNNING)
    processed = []
    logged = []

    class _NodeModel:
        @staticmethod
        def filter(**kwargs):
            if kwargs.get("status") == sla.NODE_STATUS_RUNNING:
                return FakeQuery([failed_node, healthy_node])
            return FakeQuery([])

    class _TicketModel:
        @staticmethod
        def filter(**_kwargs):
            return FakeQuery([])

    async def _resolve(node, *_args):
        processed.append(node.node_id)
        if node is failed_node:
            raise RuntimeError("broken row")
        return True

    monkeypatch.setattr(sla, "TicketNodeInstance", _NodeModel)
    monkeypatch.setattr(sla, "Ticket", _TicketModel)
    monkeypatch.setattr(sla, "_resolve_auto_node", _resolve)
    monkeypatch.setattr(
        sla.logger,
        "exception",
        lambda *args, **kwargs: logged.append((args, kwargs)),
    )

    stats = await sla.sweep_once("worker-1", now=CTIME)

    assert processed == ["auto-failed", "auto-healthy"]
    assert stats["auto_resolved"] == 1
    assert len(logged) == 1


@pytest.mark.asyncio
async def test_running_auto_node_timeout_auto_fails(monkeypatch):
    """自动化节点超时直接失败，不升级也不把工单挂起。"""
    from services.ticket import engine

    ticket = make_ticket()
    node = make_node(
        node_id="auto1",
        node_type="auto",
        status=sla.NODE_STATUS_RUNNING,
        deadline=CTIME,
    )
    activities = []

    @asynccontextmanager
    async def _fake_tx():
        yield object()

    async def _claim(*_args, **_kwargs):
        return ticket

    async def _not_escalated(*_args, **_kwargs):
        return False

    async def _record(_ticket, action, **_kwargs):
        activities.append(action)

    advanced = []

    async def _advance(*_args, **kwargs):
        advanced.append(kwargs)
        return True

    monkeypatch.setattr(sla, "in_transaction", _fake_tx)
    monkeypatch.setattr(sla, "_claim_ticket", _claim)
    monkeypatch.setattr(sla, "already_escalated", _not_escalated)
    monkeypatch.setattr(engine, "record_activity", _record)
    monkeypatch.setattr(engine, "advance_ticket", _advance)

    assert await sla._handle_node_timeout(node, "worker-1", CTIME) is True
    assert advanced[0]["decision"] == "timeout"
    assert advanced[0]["trigger"] == "sla"
    assert ticket.status != TICKET_STATUS_SUSPENDED
    assert activities == ["sla_timeout"]


# ---------- 数字伙伴过期传播 ----------

@pytest.mark.asyncio
async def test_agent_ticket_expiry_marks_command_approval_expired(monkeypatch):
    """SPEC §8.2：整个 approvement 目录删除后，expire_pending_approvals 里
    「同步把 CommandApproval 置 EXPIRED」这条必须由调度器接手。"""
    from tests.ticket.conftest import FakeQuery

    ticket = make_ticket(source=2)
    ticket.origin_params = {"command_approval_id": "ca-1"}
    approval = type("CA", (), {"id": "ca-1", "status": "pending"})()
    fake = FakeQuery([approval])
    monkeypatch.setattr(
        sla, "CommandApproval", type("M", (), {"filter": staticmethod(lambda **k: fake)})
    )

    await sla.propagate_agent_expiry(ticket)
    assert fake.update_calls, "必须发出 update 把 CommandApproval 置过期"
    assert "expired" in str(fake.update_calls[0]).lower()


@pytest.mark.asyncio
async def test_non_agent_ticket_skips_propagation(monkeypatch):
    called = []
    monkeypatch.setattr(
        sla, "CommandApproval",
        type("M", (), {"filter": staticmethod(lambda **k: called.append(k))}),
    )
    await sla.propagate_agent_expiry(make_ticket(source=0))
    assert called == []


# ---------- 挂起超阈值告警 ----------

def test_suspended_too_long_is_flagged():
    """SPEC §4.7：挂起超阈值要告警升级，否则「可被管理员发现」
    会退化成「没人发现」。"""
    assert sla.suspension_overdue(
        CTIME - timedelta(seconds=sla.SUSPENSION_ALERT_SECONDS + 1), now=CTIME
    ) is True
    assert sla.suspension_overdue(CTIME - timedelta(seconds=10), now=CTIME) is False
    assert sla.suspension_overdue(None, now=CTIME) is False
