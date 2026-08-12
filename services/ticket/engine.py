# coding=utf-8
"""工单流程引擎。

事件驱动状态机，不是常驻循环。四个触发点（SPEC §4.3）：
    submit / operate / auto_result / sla

每次推进是一个事务：校验工单非终态 → 写节点状态 → 计算下一批节点
→ 激活它们 → 记活动日志。

两条硬约束，违反任一条都会出现重复推进或死锁：
1. 锁**工单行**而非节点行。两条并行支路同时完成时，锁各自节点行锁到的是
   不同行，两边都会放行 → 重复汇聚。
2. 所有入口先校验工单非终态。行锁只防同一行并发，防不住 stale 支路
   越过汇聚后回来推进一个已终态工单。
"""

from datetime import datetime
import inspect

from tortoise.transactions import in_transaction

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.ticket import (
    APPROVE_TYPE_ANY,
    NODE_STATUS_ABNORMAL,
    NODE_STATUS_APPROVED,
    NODE_STATUS_PENDING,
    NODE_STATUS_REJECTED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_TIMEOUT,
    NODE_TERMINAL_STATUSES,
    NODE_TRANSITIONS,
    NODE_TYPE_AUTO,
    NODE_TYPE_CONDITION,
    NODE_TYPE_END,
    RESULT_KEY_CANCELLED,
    TICKET_STATUS_COMPLETED,
    TICKET_STATUS_SUSPENDED,
    TICKET_TERMINAL_STATUSES,
    TICKET_TRANSITIONS,
    Ticket,
    TicketActivity,
    TicketFlowVersion,
    TicketNodeInstance,
    TicketType,
)
from services.ticket import validators
from services.ticket.sla_scheduler import calculate_sla
from settings import TZ

_REJECT_LIKE = (NODE_STATUS_REJECTED, NODE_STATUS_TIMEOUT)


def can_transition_ticket(current: int, target: int) -> bool:
    return target in TICKET_TRANSITIONS.get(current, frozenset())


def can_transition_node(current: int, target: int) -> bool:
    return target in NODE_TRANSITIONS.get(current, frozenset())


def _nodes_index(flow_json) -> dict:
    return {n.get("id"): n for n in (flow_json or {}).get("nodes", []) if n.get("id")}


def _node_def(flow_json, node_id) -> dict | None:
    return _nodes_index(flow_json).get(node_id)


def _out_edges(flow_json, node_id) -> list[dict]:
    return [e for e in (flow_json or {}).get("edges", []) if e.get("source") == node_id]


def _eval_condition(condition: dict, form_data: dict) -> bool:
    """条件求值。字段缺失一律返回 False，让流程落到默认分支。"""
    if not condition:
        return False
    actual = (form_data or {}).get(condition.get("field"))
    expected = condition.get("value")
    op = condition.get("op", "eq")
    if actual is None:
        return False
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "gt":
            return float(actual) > float(expected)
        if op == "gte":
            return float(actual) >= float(expected)
        if op == "lt":
            return float(actual) < float(expected)
        if op == "lte":
            return float(actual) <= float(expected)
        if op == "in":
            return actual in (expected or [])
        if op == "contains":
            return str(expected) in str(actual)
    except (TypeError, ValueError):
        return False
    return False


def next_node_ids(flow_json, node_id, outcome, form_data) -> list[str]:
    """算出 node_id 达成 outcome 后应激活的下一批节点。"""
    node = _node_def(flow_json, node_id)
    if node is None or node.get("type") == NODE_TYPE_END:
        return []
    edges = _out_edges(flow_json, node_id)
    if not edges:
        return []
    if node.get("type") == NODE_TYPE_CONDITION:
        for edge in edges:
            if edge.get("condition") and _eval_condition(edge["condition"], form_data):
                return [edge["target"]]
        for edge in edges:
            if edge.get("default"):
                return [edge["target"]]
        return []

    wanted = "reject" if outcome in _REJECT_LIKE else "approve"
    labelled = [e for e in edges if e.get("on")]
    if labelled:
        picked = [e["target"] for e in labelled if e.get("on") == wanted]
        return picked or [e["target"] for e in edges if not e.get("on")]
    return [e["target"] for e in edges]


def load_flow_json(ticket, version=None) -> dict:
    """从显式版本或已预取关联中取得工单钉住的流程定义。"""
    if version is None:
        version = getattr(ticket, "_prefetched_flow_version", None)
    if version is None:
        version = getattr(ticket, "_flow_version", None)
    flow_json = getattr(version, "flow_json", None) if version is not None else None
    if not isinstance(flow_json, dict) or not flow_json:
        raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)
    return flow_json


async def record_activity(
    ticket, action, content=None, actor_id=None, node_instance=None,
    payload=None, connection=None,
) -> None:
    await TicketActivity.create(
        using_db=connection,
        tenant_id=ticket.tenant_id,
        ticket_id=ticket.id,
        node_instance_id=getattr(node_instance, "id", None),
        actor_id=actor_id,
        action=action,
        content=content,
        payload=payload or {},
    )


async def _refresh_ticket_sla(ticket, connection=None) -> None:
    """Recalculate SLA from the creation snapshot and current type config."""
    ticket_type = await TicketType.filter(
        id=ticket.ticket_type_id,
        tenant_id=ticket.tenant_id,
        deleted=False,
    ).using_db(connection).first()
    sla_config = getattr(ticket_type, "sla_config", None) if ticket_type else None
    ticket.sla_respond_at, ticket.sla_resolve_at = calculate_sla(
        ticket.ctime, ticket.priority, sla_config
    )
    await ticket.save(
        update_fields=["sla_respond_at", "sla_resolve_at", "utime"],
        using_db=connection,
    )


async def mark_node_abnormal(node, ticket, reason, connection=None) -> None:
    if not can_transition_node(node.status, NODE_STATUS_ABNORMAL):
        return
    node.status = NODE_STATUS_ABNORMAL
    await node.save(update_fields=["status", "utime"], using_db=connection)
    if can_transition_ticket(ticket.status, TICKET_STATUS_SUSPENDED):
        ticket.status = TICKET_STATUS_SUSPENDED
        ticket.suspended_at = datetime.now(TZ)
        await ticket.save(update_fields=["status", "suspended_at", "utime"], using_db=connection)
    await record_activity(
        ticket, "exception", content=reason, node_instance=node,
        payload={"node_id": node.node_id, "reason": reason}, connection=connection,
    )


def _node_deadline(definition, now):
    from datetime import timedelta

    duration = ((definition.get("sla") or {}).get("duration"))
    return now + timedelta(seconds=int(duration)) if duration else None


async def activate_nodes(ticket, node_ids, flow_json, connection=None) -> list:
    now = datetime.now(TZ)
    activated = []
    for node_id in node_ids:
        definition = _node_def(flow_json, node_id) or {}
        node_type = definition.get("type") or "approve"
        is_auto = node_type == NODE_TYPE_AUTO
        instance = await TicketNodeInstance.create(
            using_db=connection,
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.id,
            node_id=node_id,
            node_name=definition.get("name") or node_id,
            node_type=node_type,
            status=NODE_STATUS_RUNNING if is_auto else NODE_STATUS_PENDING,
            approve_type=definition.get("approve_type") or APPROVE_TYPE_ANY,
            results={}, form_data={}, deadline=_node_deadline(definition, now),
            fork_id=definition.get("fork_id"), branch_key=definition.get("branch_key"),
            entered_at=now,
        )
        await record_activity(
            ticket, "auto_action" if is_auto else "assign",
            content=f"节点激活：{instance.node_name}", node_instance=instance,
            payload={"node_id": node_id}, connection=connection,
        )
        activated.append(instance)
        if is_auto:
            result = dispatch_auto_node(ticket, instance, definition, connection)
            if inspect.isawaitable(result):
                await result
        elif definition.get("approvers") or definition.get("approver_action"):
            await resolve_node_approvers(ticket, instance, definition, connection)
    return activated


async def resolve_node_approvers(ticket, node, definition, connection=None) -> None:
    from models import Account

    static_ids = definition.get("approvers") or []
    dynamic = definition.get("approver_action")
    if dynamic:
        try:
            candidate_ids = await resolve_dynamic_approvers(ticket, dynamic)
        except Exception as exc:
            await mark_node_abnormal(node, ticket, f"动态审批人解析失败：{exc}", connection)
            return
    else:
        candidate_ids = static_ids
    if not candidate_ids:
        await mark_node_abnormal(node, ticket, "节点未解析出有效审批人", connection)
        return
    rows = await Account.filter(
        id__in=candidate_ids, tenant_id=ticket.tenant_id, deleted=False,
    ).using_db(connection).all()
    valid = validators.effective_approver_ids(
        [(a.id, not getattr(a, "disabled", False)) for a in rows]
    )
    if not valid:
        await mark_node_abnormal(node, ticket, "审批人均无效或不属于当前租户", connection)
        return
    await node.approvers.add(
        *[a for a in rows if a.id in valid], using_db=connection,
    )
    await record_activity(
        ticket, "assign", content="审批人已绑定", node_instance=node,
        payload={"before": [], "after": valid}, connection=connection,
    )


async def resolve_dynamic_approvers(ticket, action_config) -> list[str]:
    from services.ticket.auto_action import run_action_sync

    result = await run_action_sync(ticket, action_config, timeout=30)
    payload = (result or {}).get("result")
    approvers = (payload or {}).get("approvers") if isinstance(payload, dict) else None
    if not isinstance(approvers, list) or not approvers:
        raise CustomError(ErrorCode.TICKET_APPROVER_INVALID)
    if not all(isinstance(i, str) and i for i in approvers):
        raise CustomError(ErrorCode.TICKET_APPROVER_INVALID)
    return approvers


async def dispatch_auto_node(ticket, node, definition, connection=None) -> None:
    from services.ticket.auto_action import dispatch_action

    run_id = await dispatch_action(ticket, definition.get("action") or {})
    node.auto_run_id = run_id
    await node.save(update_fields=["auto_run_id", "utime"], using_db=connection)


async def change_node_approvers(
    ticket, node, operation, target_ids, actor_id=None, connection=None,
) -> list[str]:
    """在同一事务内应用转派/加签，并清理失效的处理结果。"""
    after = await validators.validate_approver_change(
        node, operation, target_ids, actor_id=actor_id,
    )
    from models import Account

    accounts = await Account.filter(
        id__in=after, tenant_id=ticket.tenant_id, deleted=False, disabled=False,
    ).all()
    by_id = {account.id: account for account in accounts}
    if set(by_id) != set(after):
        raise CustomError(ErrorCode.TICKET_APPROVER_INVALID)
    current = await node.approvers.all()
    if current:
        await node.approvers.remove(*current, using_db=connection)
    await node.approvers.add(
        *[by_id[account_id] for account_id in after], using_db=connection,
    )
    node.results = validators.purge_stale_results(after, node.results)
    await node.save(update_fields=["results", "utime"], using_db=connection)
    await record_activity(
        ticket, operation, actor_id=actor_id, node_instance=node,
        payload={"after": after, "results": node.results}, connection=connection,
    )
    return after


async def advance_ticket(
    ticket_id, tenant_id=None, trigger="operate", actor_id=None,
    node_instance_id=None, decision=None, connection=None,
    executor=None,
) -> bool:
    """推进工单一步。返回是否真的推进了（被守卫拦下返回 False）。"""
    # 外部回调使用工单对象调用；保留旧的 id/tenant/trigger 形式供内部调用。
    if hasattr(ticket_id, "id") and tenant_id is None:
        ticket = ticket_id
        ticket_id = ticket.id
        tenant_id = ticket.tenant_id
        trigger = "callback" if executor else trigger
    async with in_transaction() as conn:
        ticket = (
            await Ticket.filter(id=ticket_id, tenant_id=tenant_id, deleted=False)
            .using_db(conn).select_for_update().first()
        )
        if ticket is None:
            raise CustomError(ErrorCode.TICKET_NOT_FOUND)
        if ticket.status in TICKET_TERMINAL_STATUSES:
            return False
        if ticket.status == TICKET_STATUS_SUSPENDED and trigger != "recover":
            return False

        version = await TicketFlowVersion.filter(
            id=ticket.flow_version_id, deleted=False
        ).using_db(conn).first()
        flow_json = load_flow_json(ticket, version=version)
        node = None
        if node_instance_id:
            node = (
                await TicketNodeInstance.filter(
                    id=node_instance_id, tenant_id=tenant_id, deleted=False
                ).using_db(conn).select_for_update().first()
            )
            if (
                node is None
                or node.status in NODE_TERMINAL_STATUSES
                or (node.results or {}).get(RESULT_KEY_CANCELLED)
            ):
                return False
            outcome = await _apply_decision(ticket, node, actor_id, decision, conn)
            if outcome is None:
                return True
        else:
            outcome = NODE_STATUS_APPROVED

        source_node_id = node.node_id if node else _start_node_id(flow_json)
        form_data = {**(ticket.form_data or {}), **((node.form_data if node else {}) or {})}
        next_node_ids_before_filter = next_node_ids(
            flow_json, source_node_id, outcome, form_data
        )
        targets = next_node_ids_before_filter
        try:
            from services.ticket.merge import filter_merge_ready
        except ImportError:
            filter_merge_ready = None
        if filter_merge_ready is not None:
            targets = await filter_merge_ready(
                ticket, next_node_ids_before_filter, flow_json, conn
            )
            if next_node_ids_before_filter and not targets:
                if node is not None and outcome is not None:
                    await _refresh_ticket_sla(ticket, conn)
                return True
        terminal = _terminal_status_for(flow_json, targets)
        if terminal is not None:
            await _close_ticket(ticket, terminal, actor_id, conn)
        elif targets:
            await activate_nodes(ticket, targets, flow_json, conn)
        elif node is not None:
            await mark_node_abnormal(ticket=ticket, node=node,
                                     reason="节点无有效后继", connection=conn)
            return True
        if (
            ticket.status not in TICKET_TERMINAL_STATUSES
            and node is not None
            and outcome is not None
        ):
            await _refresh_ticket_sla(ticket, conn)
        return True


def _start_node_id(flow_json) -> str | None:
    for node in (flow_json or {}).get("nodes", []):
        if node.get("type") == "start":
            return node.get("id")
    return None


def _terminal_status_for(flow_json, target_ids) -> int | None:
    for node_id in target_ids or []:
        definition = _node_def(flow_json, node_id) or {}
        if definition.get("type") == NODE_TYPE_END:
            return definition.get("terminal_status", TICKET_STATUS_COMPLETED)
    return None


async def _apply_decision(ticket, node, actor_id, decision, conn):
    approver_ids = [a.id for a in await node.approvers.all()]
    if actor_id and actor_id not in approver_ids:
        raise CustomError(ErrorCode.TICKET_NOT_APPROVER)
    results = validators.purge_stale_results(approver_ids, node.results)
    if actor_id not in results and actor_id:
        results[actor_id] = {
            "result": decision,
            "at": datetime.now(TZ).isoformat(timespec="seconds"),
        }
    node.results = results
    outcome = validators.evaluate_node_outcome(node.approve_type, approver_ids, results)
    update_fields = ["results", "utime"]
    if outcome is not None and can_transition_node(node.status, outcome):
        node.status = outcome
        node.completed_at = datetime.now(TZ)
        update_fields += ["status", "completed_at"]
    await node.save(update_fields=update_fields, using_db=conn)
    await record_activity(
        ticket, decision or "approve", actor_id=actor_id, node_instance=node,
        payload={"node_id": node.node_id, "outcome": outcome}, connection=conn,
    )
    return outcome


async def _close_ticket(ticket, terminal_status, actor_id, conn) -> None:
    if not can_transition_ticket(ticket.status, terminal_status):
        return
    ticket.status = terminal_status
    ticket.closed_at = datetime.now(TZ)
    await ticket.save(update_fields=["status", "closed_at", "utime"], using_db=conn)
    await record_activity(
        ticket, "approve" if terminal_status == TICKET_STATUS_COMPLETED else "reject",
        content="工单已归档", actor_id=actor_id,
        payload={"status": terminal_status}, connection=conn,
    )
    from services.ticket import callbacks

    await callbacks.on_ticket_closed(ticket, actor_id, connection=conn)


async def return_to_node(ticket, return_target, actor_id, connection):
    """退回工单到指定节点或提单人（SPEC §5.4 return 操作）。

    connection 必传——调用方负责事务边界。原实现用
    `await in_transaction().__aenter__()` 手工进入上下文且从不退出，
    会泄漏连接。

    Args:
        ticket: 工单实例
        return_target: "creator" 或目标 node_id
        actor_id: 操作人 ID
        connection: 数据库连接（事务上下文）

    逻辑：
        1. 挂起当前所有运行节点（状态改为 pending）
        2. 如果 return_target == "creator"，重新激活开始节点
        3. 如果 return_target 是 node_id，激活该节点（需校验是否在已完成节点中）
        4. 记录 return 活动
    """

    # 1. 挂起所有运行中的节点
    running_nodes = await TicketNodeInstance.filter(
        ticket_id=ticket.id,
        status=NODE_STATUS_RUNNING,
        deleted=False,
    ).all()

    for node in running_nodes:
        node.status = NODE_STATUS_PENDING
        await node.save(update_fields=["status", "utime"], using_db=connection)

    # 2. 加载流程定义
    flow_version = await TicketFlowVersion.filter(
        id=ticket.flow_version_id,
        deleted=False,
    ).first()
    if not flow_version:
        raise CustomError(ErrorCode.TICKET_FLOW_NOT_FOUND)

    flow_json = flow_version.flow_json

    # 3. 确定目标节点并激活
    if return_target == "creator":
        # 退回提单人：激活开始节点
        start_node = next((n for n in flow_json["nodes"] if n.get("type") == "start"), None)
        if not start_node:
            raise CustomError(ErrorCode.TICKET_FLOW_VALIDATE_FAILED)
        target_node_ids = [start_node["id"]]
    else:
        # 退回指定节点：校验该节点是否存在且已完成
        target_node_def = next((n for n in flow_json["nodes"] if n["id"] == return_target), None)
        if not target_node_def:
            raise CustomError(ErrorCode.TICKET_RETURN_TARGET_INVALID)

        # 检查该节点是否曾被执行过（必须真取到，不要用 getattr 探测）
        historical_node = await TicketNodeInstance.filter(
            ticket_id=ticket.id,
            node_id=return_target,
            tenant_id=ticket.tenant_id,
            deleted=False,
        ).using_db(connection).first()
        if historical_node is None or historical_node.status not in NODE_TERMINAL_STATUSES:
            raise CustomError(ErrorCode.TICKET_RETURN_TARGET_INVALID)

        target_node_ids = [return_target]

    # 4. 激活目标节点
    await activate_nodes(ticket, target_node_ids, flow_json, connection=connection)

    # 5. 记录活动
    await record_activity(
        ticket, "return", actor_id=actor_id,
        content=f"退回至{'提单人' if return_target == 'creator' else '节点 ' + return_target}",
        payload={"return_target": return_target},
        connection=connection,
    )
