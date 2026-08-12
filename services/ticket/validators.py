# coding=utf-8
"""会签/或签规则与审批人变更校验。

核心差异（对比现网 services/approvement/views.py:373-376）：
现网用 len(results) == len(approvers) 判会签达成，转派/加签一改集合就崩。
本模块的法定基准是「当前 approvers 去重后人数」，实时计算而非激活时快照，
并在每次集合变更后清理不再属于 approvers 的 results，维持不变量：
    results 中的人 ⊆ 当前 approvers
"""

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from models.ticket import (
    APPROVE_TYPE_ALL,
    NODE_STATUS_APPROVED,
    NODE_STATUS_PENDING,
    NODE_STATUS_REJECTED,
    NODE_STATUS_RUNNING,
)

_ACTIVE_NODE_STATUSES = (NODE_STATUS_PENDING, NODE_STATUS_RUNNING)


def _dedup(ids) -> list[str]:
    """保序去重。顺序稳定是为了让活动日志里的快照可比对。"""
    seen, out = set(), []
    for value in ids:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def effective_approver_ids(id_enabled_pairs) -> list[str]:
    """从 (account_id, enabled) 序列里筛出仍然有效的审批人。

    被禁用/删除的人不计入法定基准。会签下全部失效时返回空列表，
    调用方须据此转 5异常，**不得视为通过**。
    """
    return _dedup(acc_id for acc_id, enabled in id_enabled_pairs if enabled)


def purge_stale_results(approver_ids, results) -> dict:
    """丢弃不在当前 approvers 里的处理记录，维持 results ⊆ approvers。

    转派后原审批人的记录必须作废，否则会签会用一个已不在集合里的
    「通过」凑够人数。纯函数，不改入参。
    """
    allowed = set(_dedup(approver_ids))
    return {k: v for k, v in (results or {}).items() if k in allowed}


def evaluate_node_outcome(approve_type, approver_ids, results) -> int | None:
    """判定节点是否达终态。返回 NODE_STATUS_APPROVED / REJECTED，未达成返回 None。

    这是纯函数，是会签规则的唯一口径——引擎与超时调度器都调它，
    避免两处各写一套判定而出现分歧。
    """
    approvers = _dedup(approver_ids)
    valid = purge_stale_results(approvers, results)

    # 任一拒绝即整体拒绝，或签会签同规则，且不继续收集
    if any(r.get("result") == "reject" for r in valid.values()):
        return NODE_STATUS_REJECTED

    approved = [k for k, v in valid.items() if v.get("result") == "approve"]
    if not approvers:
        # 无有效审批人：绝不静默通过（安全项，SPEC §4.4/§4.5）
        return None

    if approve_type == APPROVE_TYPE_ALL:
        return NODE_STATUS_APPROVED if len(approved) >= len(approvers) else None
    return NODE_STATUS_APPROVED if approved else None


async def check_quorum_reached(node) -> bool:
    """节点当前是否已达成结论（供加签前置校验用）。"""
    approver_ids = [a.id for a in await node.approvers.all()]
    return evaluate_node_outcome(node.approve_type, approver_ids, node.results) is not None


async def validate_approver_change(
    node, operation: str, target_ids, actor_id: str | None = None,
    resolve_accounts=None,
) -> list[str]:
    """校验审批人变更并返回**变更后**的审批人 id 集合（已去重）。

    只做校验与集合计算，不落库、不写活动日志——留给引擎在同一事务里做，
    这样审批人快照与状态推进原子一致。

    operation: transfer | add_approver | assign_approver
    resolve_accounts: 可注入的账号解析函数，默认查库校验同租户且启用。
    """
    if node.status not in _ACTIVE_NODE_STATUSES:
        raise CustomError(ErrorCode.TICKET_NODE_NOT_ACTIVE)

    targets = _dedup(target_ids or [])
    if not targets:
        raise CustomError(ErrorCode.PARAM_ERROR)

    # 加签前置：已达成则节点已终态，禁止加签（SPEC §4.5）
    if operation == "add_approver" and await check_quorum_reached(node):
        raise CustomError(ErrorCode.TICKET_QUORUM_REACHED)

    resolved = await _resolve_targets(node, targets, resolve_accounts)
    if len(resolved) != len(targets):
        raise CustomError(ErrorCode.TICKET_APPROVER_INVALID)

    current = [a.id for a in await node.approvers.all()]
    if operation == "transfer":
        if not actor_id:
            raise CustomError(ErrorCode.PARAM_ERROR)
        if actor_id not in current:
            raise CustomError(ErrorCode.TICKET_NOT_APPROVER)
        # 集合替换：移走 actor，加入目标。目标已在集合内则去重，法定人数 -1
        remaining = [i for i in current if i != actor_id]
        return _dedup(remaining + resolved)

    if operation in ("add_approver", "assign_approver"):
        return _dedup(current + resolved)

    raise CustomError(ErrorCode.PARAM_ERROR)


async def _resolve_targets(node, target_ids, resolve_accounts=None):
    """解析目标账号，要求同租户且启用。跨租户/禁用/不存在都算无效。"""
    if resolve_accounts is not None:
        result = resolve_accounts(target_ids)
        if hasattr(result, "__await__"):
            result = await result
        return _dedup(getattr(a, "id", a) for a in result)

    from models import Account

    rows = await Account.filter(
        id__in=target_ids, tenant_id=node.tenant_id, deleted=False, disabled=False
    ).all()
    return _dedup(a.id for a in rows)
