# coding=utf-8
"""SLA 计算与后台调度。

现网是惰性扫描（挂在 list/todolist/SSE 查询上，
services/approvement/views.py:40-77），做不到「到期前预警」，因此引入真调度器。

**合并为单一后台任务**：不为「自动节点结果轮询」和「SLA 扫描」各起一个循环。
每 60 秒一轮做三件事（SPEC §4.7）：
1. 扫 status=4执行中 的节点，取 Runlog 结果推进
2. 扫节点 deadline 与工单 sla_respond_at/sla_resolve_at，触发预警/超时动作
3. 扫 6异常挂起 超阈值的工单，告警升级

**互斥不假定 worker 数**：复用 command_center 的 claim/lease 范式
（approval_service.py:56 的 RECOVERY_CLAIM_TTL_SECONDS = 300）——所有 worker
都扫，处理某行前先 claim 该行，claim 失败即跳过。不用 SETNX 选主：worker 数
变化或锁 TTL 到期而任务未完成时它都会退化。
"""

from datetime import datetime, timedelta

from sanic.log import logger
from tortoise.transactions import in_transaction

from models import CommandApproval
from models.ticket import (
    NODE_STATUS_APPROVED,
    NODE_STATUS_PENDING,
    NODE_STATUS_REJECTED,
    NODE_STATUS_RUNNING,
    TICKET_STATUS_PROCESSING,
    TICKET_STATUS_SUSPENDED,
    TICKET_STATUS_TIMEOUT,
    Ticket,
    TicketActivity,
    TicketNodeInstance,
    TicketType,
)
from settings import TZ

# 与 command_center 保持同一租约语义，避免两套超时口径
CLAIM_TTL_SECONDS = 300
DEFAULT_WARN_BEFORE_SECONDS = 900
SUSPENSION_ALERT_SECONDS = 3600

# 节点超时策略 → 节点终态。未知策略与 notify_only/transfer 都返回 None
# （不改节点状态），**绝不映射成通过**——静默通过审批节点是安全漏洞。
_TIMEOUT_OUTCOMES = {
    "auto_approve": NODE_STATUS_APPROVED,
    "auto_reject": NODE_STATUS_REJECTED,
}


def calculate_sla(ctime, priority, sla_config, flow_deadline=None):
    """算工单级响应/解决时限。

    公式以 ctime 为锚点（不是改动时刻），所以优先级中途被表单改动时
    直接重算即可。缺当前优先级键则两个字段留 None，不报错。

    flow_deadline 是剧本触发场景的外层边界（SPEC §2.2）：
    Redis 上游输出 TTL 与剧本 flow_timeout 对齐，工单过期时间取更早者，
    否则缓存先于审批过期，恢复失败。
    """
    config = sla_config or {}
    bucket = config.get(str(priority)) or {}
    respond = _plus(ctime, bucket.get("respond"))
    resolve = _plus(ctime, bucket.get("resolve"))

    if flow_deadline is not None:
        # 即便没配 SLA，也要受剧本超时约束，否则工单永不终结
        resolve = flow_deadline if resolve is None else min(resolve, flow_deadline)
    return respond, resolve


def _plus(anchor, seconds):
    if not seconds or anchor is None:
        return None
    return anchor + timedelta(seconds=int(seconds))


def node_deadline(entered_at, node_sla, flow_deadline=None):
    """节点 deadline = entered_at + node.sla.duration。节点级不按优先级分档。"""
    deadline = _plus(entered_at, (node_sla or {}).get("duration"))
    if flow_deadline is not None and deadline is not None:
        return min(deadline, flow_deadline)
    return deadline or flow_deadline


def shift_for_suspension(deadline, suspended_at, resumed_at):
    """挂起期间 SLA 暂停：把挂起时长顺延到 deadline 上。"""
    if deadline is None or suspended_at is None or resumed_at is None:
        return deadline
    paused = resumed_at - suspended_at
    return deadline + paused if paused.total_seconds() > 0 else deadline


def should_warn(deadline, warn_before, last_sweep, now) -> bool:
    """预警点是否落在 [上次扫描, 本次扫描) 区间内——保证只触发一次。"""
    if deadline is None:
        return False
    warn_at = deadline - timedelta(seconds=int(warn_before or DEFAULT_WARN_BEFORE_SECONDS))
    if last_sweep is None:
        return warn_at <= now
    return last_sweep <= warn_at < now


def timeout_outcome(policy) -> int | None:
    """超时策略映射。只有自动通过和自动拒绝会改变节点状态。"""
    return _TIMEOUT_OUTCOMES.get(policy)


def notify_only_allowed(sla_config, priority) -> bool:
    """「仅通知」必须有工单级 resolve 兜底，否则工单无法终结。"""
    bucket = (sla_config or {}).get(str(priority)) or {}
    return bool(bucket.get("resolve"))


def suspension_overdue(suspended_at, now=None) -> bool:
    if suspended_at is None:
        return False
    return (now or datetime.now(TZ)) - suspended_at > timedelta(seconds=SUSPENSION_ALERT_SECONDS)


async def claim_row(
    row,
    owner: str,
    ttl: int = CLAIM_TTL_SECONDS,
    now=None,
    connection=None,
) -> bool:
    """claim 一行，成功返回 True。

    真正的并发互斥依赖 DB 行锁；sweep 通过 ``_claim_ticket`` 在
    ``select_for_update`` 事务内调用。单测直接调用时只验证租约判定逻辑。
    """
    moment = now or datetime.now(TZ)
    holder = getattr(row, "sweep_owner", None)
    expires = getattr(row, "sweep_expires_at", None)
    if holder and holder != owner and expires and expires > moment:
        return False
    row.sweep_owner = owner
    row.sweep_expires_at = moment + timedelta(seconds=ttl)
    await row.save(
        update_fields=["sweep_owner", "sweep_expires_at", "utime"],
        using_db=connection,
    )
    return True


async def already_escalated(node, activities_provider=None, connection=None) -> bool:
    """该节点是否已发过超时通知。活动表记录保证重复扫描幂等。"""
    if activities_provider is not None:
        return any(
            activity.get("node_instance_id") == node.id
            and activity.get("action") == "sla_escalate"
            for activity in activities_provider()
        )
    return await TicketActivity.filter(
        node_instance_id=node.id,
        action="sla_escalate",
        deleted=False,
    ).using_db(connection).exists()


async def already_warned(
    ticket, node=None, activities_provider=None, connection=None
) -> bool:
    """该工单/节点是否已发过预警，保证周期扫描幂等。"""
    node_id = getattr(node, "id", None)
    if activities_provider is not None:
        return any(
            activity.get("ticket_id") == ticket.id
            and activity.get("node_instance_id") == node_id
            and activity.get("action") == "sla_warn"
            for activity in activities_provider()
        )
    return await TicketActivity.filter(
        ticket_id=ticket.id,
        node_instance_id=node_id,
        action="sla_warn",
        deleted=False,
    ).using_db(connection).exists()


async def _ticket_warn_before(ticket, connection=None) -> int:
    """读取工单类型的预警提前量，缺省使用统一值。"""
    ticket_type = getattr(ticket, "_prefetched_ticket_type", None)
    if ticket_type is None and getattr(ticket, "ticket_type_id", None):
        ticket_type = await TicketType.filter(
            id=ticket.ticket_type_id,
            tenant_id=ticket.tenant_id,
            deleted=False,
        ).using_db(connection).first()
    return int(
        ((getattr(ticket_type, "sla_config", None) or {}).get("warn_before"))
        or DEFAULT_WARN_BEFORE_SECONDS
    )


async def _record_sla_warning(
    ticket, owner, moment, deadline, last_sweep, node=None
) -> bool:
    """为一个节点或工单记录一次预警。"""
    if ticket is None or deadline is None or deadline <= moment:
        return False
    warn_before = await _ticket_warn_before(ticket)
    if not should_warn(deadline, warn_before, last_sweep, moment):
        return False
    async with in_transaction() as connection:
        claimed = await _claim_ticket(ticket if node is None else node, owner, moment, connection)
        if claimed is None or await already_warned(
            claimed, node=node, connection=connection
        ):
            return False
        from services.ticket.engine import record_activity

        await record_activity(
            claimed,
            "sla_warn",
            content="SLA 即将到期",
            node_instance=node,
            payload={"deadline": deadline.isoformat(), "warn_before": warn_before},
            connection=connection,
        )
        return True


async def propagate_agent_expiry(ticket, connection=None) -> None:
    """source=2 的工单过期时，同步把 CommandApproval 置 EXPIRED。"""
    if getattr(ticket, "source", 0) != 2:
        return
    approval_id = (ticket.origin_params or {}).get("command_approval_id")
    if not approval_id:
        return
    from models.command_center import COMMAND_APPROVAL_STATUS_EXPIRED

    await CommandApproval.filter(
        id=approval_id,
        deleted=False,
    ).using_db(connection).update(status=COMMAND_APPROVAL_STATUS_EXPIRED)


async def sweep_once(owner: str, now=None, last_sweep=None) -> dict:
    """一轮扫描。三件事合一，返回各项处理计数供日志与测试断言。"""
    moment = now or datetime.now(TZ)
    stats = {
        "auto_resolved": 0,
        "warned": 0,
        "timed_out": 0,
        "suspension_alerts": 0,
    }

    # 1) 在途自动节点取结果
    running = await TicketNodeInstance.filter(
        status=NODE_STATUS_RUNNING,
        deleted=False,
    ).all()
    for node in running:
        try:
            if await _resolve_auto_node(node, owner, moment):
                stats["auto_resolved"] += 1
        except Exception:
            logger.exception(
                "工单调度单行处理失败: stage=auto_result node_id=%s",
                getattr(node, "id", None),
            )
            continue

    # 2) 未到期的 SLA 预警

    # 2) 未到期的 SLA 预警
    warning_nodes = await TicketNodeInstance.filter(
        status__in=[NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
        deleted=False,
        deadline__gt=moment,
    ).all()
    for node in warning_nodes:
        try:
            if await _record_sla_warning(
                await _ticket_for_node(node), owner, moment,
                node.deadline, last_sweep, node=node,
            ):
                stats["warned"] += 1
        except Exception:
            logger.exception(
                "工单调度单行处理失败: stage=node_warning node_id=%s",
                getattr(node, "id", None),
            )
            continue

    warning_tickets = {}
    for deadline_field in ("sla_respond_at__gt", "sla_resolve_at__gt"):
        rows = await Ticket.filter(
            status=TICKET_STATUS_PROCESSING,
            deleted=False,
            **{deadline_field: moment},
        ).all()
        warning_tickets.update({ticket.id: ticket for ticket in rows})
    for ticket in warning_tickets.values():
        try:
            for deadline in (ticket.sla_respond_at, ticket.sla_resolve_at):
                if await _record_sla_warning(
                    ticket, owner, moment, deadline, last_sweep
                ):
                    stats["warned"] += 1
                    break
        except Exception:
            logger.exception(
                "工单调度单行处理失败: stage=ticket_warning ticket_id=%s",
                getattr(ticket, "id", None),
            )
            continue

    # 节点 deadline 与工单 SLA
    overdue_nodes = await TicketNodeInstance.filter(
        status__in=[NODE_STATUS_PENDING, NODE_STATUS_RUNNING],
        deleted=False,
        deadline__lte=moment,
    ).all()
    for node in overdue_nodes:
        try:
            if await _handle_node_timeout(node, owner, moment):
                stats["timed_out"] += 1
        except Exception:
            logger.exception(
                "工单调度单行处理失败: stage=node_timeout node_id=%s",
                getattr(node, "id", None),
            )
            continue

    overdue_tickets = await Ticket.filter(
        status=TICKET_STATUS_PROCESSING,
        deleted=False,
        sla_resolve_at__lte=moment,
    ).all()
    for ticket in overdue_tickets:
        try:
            if await _handle_ticket_timeout(ticket, owner, moment):
                stats["timed_out"] += 1
        except Exception:
            logger.exception(
                "工单调度单行处理失败: stage=ticket_timeout ticket_id=%s",
                getattr(ticket, "id", None),
            )
            continue

    # 3) 挂起超阈值告警
    suspended = await Ticket.filter(
        status=TICKET_STATUS_SUSPENDED,
        deleted=False,
    ).all()
    for ticket in suspended:
        try:
            if not suspension_overdue(ticket.suspended_at, moment):
                continue
            claimed = await _claim_ticket(ticket, owner, moment)
            if claimed is not None:
                logger.warning(f"工单异常挂起超阈值: ticket_no={claimed.ticket_no}")
                stats["suspension_alerts"] += 1
        except Exception:
            logger.exception(
                "工单调度单行处理失败: stage=suspension ticket_id=%s",
                getattr(ticket, "id", None),
            )
            continue
    return stats


async def _ticket_for_node(node):
    """取节点所属工单；调度器按节点逐行扫描，避免依赖关系预取。"""
    ticket = getattr(node, "_ticket", None)
    if ticket is not None:
        return ticket
    return await Ticket.filter(
        id=node.ticket_id, tenant_id=node.tenant_id, deleted=False
    ).first()


async def _claim_ticket(ticket_or_node, owner, moment, connection=None):
    """锁并 claim 节点所属 Ticket；Ticket 是调度互斥的唯一租约行。"""
    if connection is None:
        async with in_transaction() as transaction_connection:
            return await _claim_ticket(
                ticket_or_node,
                owner,
                moment,
                connection=transaction_connection,
            )

    ticket_id = getattr(ticket_or_node, "ticket_id", None) or ticket_or_node.id
    tenant_id = getattr(ticket_or_node, "tenant_id", None)
    condition = {"id": ticket_id, "deleted": False}
    if tenant_id is not None:
        condition["tenant_id"] = tenant_id
    locked = await (
        Ticket.filter(**condition)
        .using_db(connection)
        .select_for_update()
        .first()
    )
    if locked is None:
        return None
    if not await claim_row(
        locked,
        owner,
        now=moment,
        connection=connection,
    ):
        return None
    return locked


async def _resolve_auto_node(node, owner, moment) -> bool:
    """取自动节点的动作结果并推进。CAS 由引擎的节点终态守卫保证。"""
    from models import Runlog
    from services.ticket.engine import advance_ticket, mark_node_abnormal

    if not node.auto_run_id:
        return False
    async with in_transaction() as connection:
        ticket = await _claim_ticket(node, owner, moment, connection=connection)
        if ticket is None:
            return False
        log = await Runlog.filter(
            run_id=node.auto_run_id,
            tenant_id=node.tenant_id,
            deleted=False,
        ).using_db(connection).order_by("-utime").first()
        if log is None:
            return False
        if log.status in (-1, 2):
            await mark_node_abnormal(
                node,
                ticket,
                f"自动动作执行失败: run_id={node.auto_run_id}",
                connection=connection,
            )
            return True
        if log.result in (None, "", "执行中"):
            return False
        await advance_ticket(
            node.ticket_id,
            node.tenant_id,
            trigger="auto_result",
            node_instance_id=node.id,
            decision="approve",
            connection=connection,
        )
        return True


async def _handle_node_timeout(node, owner, moment) -> bool:
    """节点超时：按策略执行，并保证通知只发一次。"""
    from services.ticket.engine import advance_ticket, mark_node_abnormal, record_activity

    async with in_transaction() as connection:
        ticket = await _claim_ticket(node, owner, moment, connection=connection)
        if ticket is None or await already_escalated(node, connection=connection):
            return False

        policy = ((node.form_data or {}).get("sla") or {}).get("on_timeout") or "notify_only"
        await record_activity(
            ticket,
            "sla_escalate",
            content=f"节点超时，策略={policy}",
            node_instance=node,
            payload={"node_id": node.node_id, "policy": policy},
            connection=connection,
        )
        if node.status == NODE_STATUS_RUNNING:
            await mark_node_abnormal(
                node,
                ticket,
                f"自动动作超时: node_id={node.node_id}",
                connection=connection,
            )
            return True
        outcome = timeout_outcome(policy)
        if outcome is None:
            return True
        await advance_ticket(
            node.ticket_id,
            node.tenant_id,
            trigger="sla",
            node_instance_id=node.id,
            decision="approve" if outcome == NODE_STATUS_APPROVED else "reject",
            connection=connection,
        )
        return True


async def _handle_ticket_timeout(ticket, owner, moment) -> bool:
    """工单级 resolve 到期：置 3已超时，并传播数字员工过期。"""
    from services.ticket import callbacks
    from services.ticket.engine import can_transition_ticket, record_activity

    async with in_transaction() as connection:
        claimed = await _claim_ticket(
            ticket,
            owner,
            moment,
            connection=connection,
        )
        if claimed is None or not can_transition_ticket(claimed.status, TICKET_STATUS_TIMEOUT):
            return False
        claimed.status = TICKET_STATUS_TIMEOUT
        claimed.closed_at = moment
        await claimed.save(
            update_fields=["status", "closed_at", "utime"],
            using_db=connection,
        )
        await record_activity(
            claimed,
            "sla_escalate",
            content="工单解决时限到期，按超时处理",
            connection=connection,
        )
        await propagate_agent_expiry(claimed, connection=connection)
    # 非「通过」一律回传 2，宁可让剧本走拒绝分支也不悬挂（SPEC §2.1）
    await callbacks.on_ticket_closed(claimed)
    return True
