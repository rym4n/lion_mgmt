# coding=utf-8
"""工单三层权限判定与 available_actions 计算（SPEC §6.2 & §7.1）。

三层权限判定顺序：
1. RBAC：决定菜单与按钮是否渲染
2. 流程级：用户对该流程有无 r/u/d/s 权限
3. 工单级：用户在这张工单上的身份（提单人/当前节点审批人/管理员）

逐层收紧，任一层失败即拒绝。后端每个接口必须完整走完三层，不能因前端
未渲染按钮就跳过校验。
"""

from libs.security.authorization import account_has_permission, is_superadmin
from models.ticket import (
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    SOURCE_PLAYBOOK,
    TICKET_STATUS_SUSPENDED,
    TICKET_TERMINAL_STATUSES,
)


def is_ticket_admin(session: dict) -> bool:
    """判断是否工单管理员（拥有 ticket:all）。"""
    account = session.get("account_info")
    if not account:
        return False
    tenant_id = session.get("tenant_id")
    return (
        is_superadmin(account, tenant_id)
        or account_has_permission(account, "ticket:all", tenant_id=tenant_id)
    )


def is_ticket_creator(ticket, user_id: str | None) -> bool:
    """判断是否工单提单人。"""
    return user_id is not None and ticket.creator_id == user_id


async def is_current_approver(node, user_id: str | None) -> bool:
    """判断用户是否当前活跃节点的审批人。

    node 必须是已 prefetch/await approvers 的实例。
    """
    if user_id is None or node is None:
        return False
    if node.status not in (NODE_STATUS_PENDING, NODE_STATUS_RUNNING):
        return False
    approver_ids = [a.id for a in await node.approvers.all()]
    return user_id in approver_ids


def compute_available_actions(
    ticket, active_approver_ids: set[str], all_approver_ids: set[str],
    user_id: str | None, session: dict,
) -> list[str]:
    """计算当前用户对该工单的可用操作集合（SPEC §7.1）。

    纯函数: 审批人集合由调用方查库解析后传入,本函数不碰 ORM。
    这样既可离线测试,也避免「预取探测失败静默退化」。

    Args:
        ticket: Ticket 实例
        active_approver_ids: 当前活跃节点的审批人并集
        all_approver_ids: 保留参数以兼容详情权限计算调用方
        user_id: 当前用户 id
        session: request.ctx.session

    Returns:
        action 列表，如 ["approve", "reject", "transfer", "return"]
    """
    actions = []

    # 终态工单所有操作不可用
    if ticket.status in TICKET_TERMINAL_STATUSES:
        return []

    is_admin = is_ticket_admin(session)
    is_creator = is_ticket_creator(ticket, user_id)

    # 异常挂起态只对管理员开放
    if ticket.status == TICKET_STATUS_SUSPENDED:
        if is_admin:
            return ["force_close"]
        return []

    # 管理员特权操作
    if is_admin:
        actions.append("force_close")

    # 提单人可催办；剧本工单同样禁止用户侧撤回（撤回已不再是详情操作）。
    if is_creator and ticket.source != SOURCE_PLAYBOOK:
        actions.append("urge")

    # 当前审批人操作
    is_active_approver = user_id in active_approver_ids if user_id else False
    if is_active_approver:
        actions.extend(["approve", "reject", "transfer", "return", "add_approver"])

    # 去重并返回
    return list(dict.fromkeys(actions))


def has_rbac_permission(session: dict, *permissions: str) -> bool:
    """检查 RBAC 权限（第一层）。"""
    account = session.get("account_info")
    if not account:
        return False
    tenant_id = session.get("tenant_id")

    if is_superadmin(account, tenant_id):
        return True

    for perm in permissions:
        if account_has_permission(account, perm, tenant_id=tenant_id):
            return True
    return False
