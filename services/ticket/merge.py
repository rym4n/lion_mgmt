# coding=utf-8
"""并行分支汇聚判定。

汇聚计数口径：**按 fork 分出的路数（N），不按汇聚节点的入边数**。

死锁反例（SPEC §4.2）：某支路内含条件分支，两条臂都直连汇聚节点 →
入边数为 2，但运行时只有一条臂被选中 → 按入边计数永远达不到 N →
永久死锁。而自闭合约束并不禁止这种画法。

因此每路无论内部如何分叉合并，只贡献一个计数：节点实例上记 branch_key，
去重统计已完成的 branch_key 数量。
"""

from datetime import datetime

from models.ticket import (
    MERGE_STRATEGY_ANY,
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    NODE_TYPE_PARALLEL,
    RESULT_KEY_CANCELLED,
    TicketNodeInstance,
)
from settings import TZ

_ACTIVE = (NODE_STATUS_PENDING, NODE_STATUS_RUNNING)


def _nodes_index(flow_json) -> dict:
    return {n.get("id"): n for n in (flow_json or {}).get("nodes", []) if n.get("id")}


def fork_branch_count(flow_json, fork_id) -> int:
    """fork 分出几路。优先取显式声明的 branches，否则数 fork 的出边。"""
    definition = _nodes_index(flow_json).get(fork_id) or {}
    branches = definition.get("branches")
    if branches:
        return len(branches)
    return len({
        e.get("target") for e in (flow_json or {}).get("edges", [])
        if e.get("source") == fork_id and e.get("target")
    })


def branch_key_of(flow_json, node_id) -> str | None:
    """节点属于哪一路。发布校验保证并行块内每个节点都带 branch_key。"""
    return (_nodes_index(flow_json).get(node_id) or {}).get("branch_key")


async def _load_nodes(ticket, fork_id, connection=None, nodes_provider=None):
    if nodes_provider is not None:
        rows = nodes_provider()
        return [n for n in rows if getattr(n, "fork_id", None) == fork_id]
    query = TicketNodeInstance.filter(
        ticket_id=ticket.id, tenant_id=ticket.tenant_id, deleted=False, fork_id=fork_id
    )
    if connection is not None:
        query = query.using_db(connection)
    return await query.all()


async def count_finished_branches(
    ticket, merge_def, connection=None, nodes_provider=None
) -> set[str]:
    """没有活跃节点的支路集合。同一 branch_key 的多个节点只算一路。"""
    fork_id = merge_def.get("fork_id")
    nodes = await _load_nodes(ticket, fork_id, connection, nodes_provider)
    by_branch = {}
    for node in nodes:
        if node.branch_key:
            by_branch.setdefault(node.branch_key, []).append(node)
    return {
        branch_key
        for branch_key, branch_nodes in by_branch.items()
        if all(node.status not in _ACTIVE for node in branch_nodes)
    }


async def _merge_instance_exists(ticket, merge_id, connection=None, nodes_provider=None):
    """汇聚节点是否已被激活过。Kafka at-least-once + 轮询重复投递都可能
    让同一汇聚被判定两次，这里做存在性 CAS。"""
    if nodes_provider is not None:
        return any(getattr(n, "node_id", None) == merge_id for n in nodes_provider())
    query = TicketNodeInstance.filter(
        ticket_id=ticket.id, tenant_id=ticket.tenant_id, deleted=False, node_id=merge_id
    )
    if connection is not None:
        query = query.using_db(connection)
    return await query.exists()


async def is_merge_ready(
    ticket, merge_def, flow_json, connection=None, nodes_provider=None
) -> bool:
    """汇聚条件是否满足。全部完成策略要 == N，任一完成策略要 >= 1。"""
    finished = await count_finished_branches(
        ticket, merge_def, connection, nodes_provider
    )
    if merge_def.get("strategy") == MERGE_STRATEGY_ANY:
        return len(finished) >= 1
    return len(finished) >= fork_branch_count(flow_json, merge_def.get("fork_id"))


async def cancel_losing_branches(
    ticket, merge_def, flow_json, connection=None, nodes_provider=None, record=None
) -> list:
    """「任一完成」fire 后取消其余支路的活跃节点。

    必须做：自动节点的 Kafka 消息可能仍在途，不取消它们回来时会推进一个
    已越过汇聚的支路。取消用 results 里的标记 + completed_at 表达，
    不新增节点状态枚举（SPEC §4.2）。

    注意这只是「尽早止损」，真正的兜底是 advance_ticket 的非终态守卫。
    """
    fork_id = merge_def.get("fork_id")
    nodes = await _load_nodes(ticket, fork_id, connection, nodes_provider)
    now = datetime.now(TZ)
    cancelled = []
    for node in nodes:
        if node.status not in _ACTIVE:
            continue
        results = dict(node.results or {})
        results[RESULT_KEY_CANCELLED] = True
        node.results = results
        node.completed_at = now
        await node.save(
            update_fields=["results", "completed_at", "utime"], using_db=connection
        )
        cancelled.append(node)
        if record is not None:
            await record(
                ticket, "exception",
                content=f"并行支路已取消：{node.node_id}",
                node_instance=node,
                payload={"node_id": node.node_id, "reason": "merge_any_fired"},
                connection=connection,
            )
    return cancelled


async def filter_merge_ready(
    ticket, target_ids, flow_json, connection=None, nodes_provider=None
) -> list[str]:
    """从待激活列表里剔除条件未满足的汇聚节点。

    每条支路完成都会把汇聚算成自己的下一跳，若不过滤，汇聚会被激活 N 次。
    """
    if not target_ids:
        return []
    index = _nodes_index(flow_json)
    passed = []
    for node_id in target_ids:
        definition = index.get(node_id) or {}
        is_merge = (
            definition.get("type") == NODE_TYPE_PARALLEL
            and definition.get("mode") == "merge"
        )
        if not is_merge:
            passed.append(node_id)
            continue
        if await _merge_instance_exists(ticket, node_id, connection, nodes_provider):
            continue     # 已激活过，本次是重复推进
        if not await is_merge_ready(
            ticket, definition, flow_json, connection, nodes_provider
        ):
            continue     # 还差支路，按住不激活
        if definition.get("strategy") == MERGE_STRATEGY_ANY:
            from services.ticket.engine import record_activity

            await cancel_losing_branches(
                ticket, definition, flow_json, connection,
                nodes_provider, record=record_activity,
            )
        passed.append(node_id)
    return passed
