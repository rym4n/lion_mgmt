# coding=utf-8
"""并行汇聚。按 fork 分出的路数计数，每路无论内部如何分叉合并只贡献一个计数。

死锁反例（SPEC §4.2）：支路内条件分支的两条臂都直连汇聚 → 入边数 2，
运行时只走 1 条 → 按入边计数永远凑不满 → 永久死锁。本模块按分支计数。
"""

import pytest

from models.ticket import (
    MERGE_STRATEGY_ALL,
    MERGE_STRATEGY_ANY,
    NODE_STATUS_APPROVED,
    NODE_STATUS_PENDING,
    NODE_STATUS_RUNNING,
    RESULT_KEY_CANCELLED,
)
from services.ticket import merge
from tests.ticket.conftest import make_node, make_ticket

# 支路 b2 内含条件分支，两条臂 b2x / b2y 都直连汇聚——按入边数会算成 3
FORK_WITH_INNER_CONDITION = {
    "nodes": [
        {"id": "fork", "type": "parallel", "mode": "fork", "branches": ["b1", "b2"]},
        {"id": "b1", "type": "approve", "fork_id": "fork", "branch_key": "b1"},
        {"id": "b2", "type": "condition", "fork_id": "fork", "branch_key": "b2"},
        {"id": "b2x", "type": "approve", "fork_id": "fork", "branch_key": "b2"},
        {"id": "b2y", "type": "approve", "fork_id": "fork", "branch_key": "b2"},
        {"id": "merge", "type": "parallel", "mode": "merge",
         "fork_id": "fork", "strategy": MERGE_STRATEGY_ALL},
    ],
    "edges": [
        {"source": "fork", "target": "b1"},
        {"source": "fork", "target": "b2"},
        {"source": "b1", "target": "merge"},
        {"source": "b2", "target": "b2x", "condition": {"field": "k", "op": "eq", "value": 1}},
        {"source": "b2", "target": "b2y", "default": True},
        {"source": "b2x", "target": "merge"},
        {"source": "b2y", "target": "merge"},
    ],
}

MERGE_DEF = FORK_WITH_INNER_CONDITION["nodes"][-1]


def test_branch_count_uses_fork_branches_not_incoming_edges():
    """入边有 3 条（b1/b2x/b2y → merge），但 fork 只分出 2 路。"""
    assert merge.fork_branch_count(FORK_WITH_INNER_CONDITION, "fork") == 2
    incoming = [e for e in FORK_WITH_INNER_CONDITION["edges"] if e["target"] == "merge"]
    assert len(incoming) == 3   # 反例存在，证明入边计数会死锁


def test_branch_count_falls_back_to_fork_out_edges():
    """流程图未显式声明 branches 时，用 fork 的出边数兜底。"""
    flow = {
        "nodes": [{"id": "f", "type": "parallel", "mode": "fork"}],
        "edges": [{"source": "f", "target": "x"}, {"source": "f", "target": "y"}],
    }
    assert merge.fork_branch_count(flow, "f") == 2


def test_branch_key_lookup():
    assert merge.branch_key_of(FORK_WITH_INNER_CONDITION, "b2y") == "b2"
    assert merge.branch_key_of(FORK_WITH_INNER_CONDITION, "b1") == "b1"


@pytest.mark.asyncio
async def test_each_branch_contributes_exactly_one_count():
    """b2 路内 b2y 完成即算该路完成，不因内部多节点重复计数。"""
    ticket = make_ticket()
    nodes = [
        make_node(node_id="b1", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b1"),
        make_node(node_id="b2", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b2"),
        make_node(node_id="b2y", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b2"),
    ]
    finished = await merge.count_finished_branches(
        ticket, MERGE_DEF, nodes_provider=lambda: nodes
    )
    assert finished == {"b1", "b2"}


@pytest.mark.asyncio
async def test_merge_all_not_ready_until_every_branch_done():
    ticket = make_ticket()
    nodes = [
        make_node(node_id="b1", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b1"),
        make_node(node_id="b2y", status=NODE_STATUS_PENDING, fork_id="fork", branch_key="b2"),
    ]
    ready = await merge.is_merge_ready(
        ticket, MERGE_DEF, FORK_WITH_INNER_CONDITION, nodes_provider=lambda: nodes
    )
    assert ready is False


@pytest.mark.asyncio
async def test_serial_branch_is_not_done_while_later_node_is_active():
    """同一支路 A 已完成但 B 仍待处理时，不能提前放行汇聚。"""
    ticket = make_ticket()
    nodes = [
        make_node(node_id="b1-a", status=NODE_STATUS_APPROVED,
                  fork_id="fork", branch_key="b1"),
        make_node(node_id="b1-b", status=NODE_STATUS_PENDING,
                  fork_id="fork", branch_key="b1"),
        make_node(node_id="b2", status=NODE_STATUS_APPROVED,
                  fork_id="fork", branch_key="b2"),
    ]

    finished = await merge.count_finished_branches(
        ticket, MERGE_DEF, nodes_provider=lambda: nodes
    )

    assert finished == {"b2"}
    assert await merge.is_merge_ready(
        ticket, MERGE_DEF, FORK_WITH_INNER_CONDITION,
        nodes_provider=lambda: nodes,
    ) is False


@pytest.mark.asyncio
async def test_merge_all_ready_when_selected_arm_finishes():
    """只有 b2y 被选中并完成，b2x 从未创建实例——仍应汇聚成功，不死锁。"""
    ticket = make_ticket()
    nodes = [
        make_node(node_id="b1", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b1"),
        make_node(node_id="b2", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b2"),
        make_node(node_id="b2y", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b2"),
    ]
    ready = await merge.is_merge_ready(
        ticket, MERGE_DEF, FORK_WITH_INNER_CONDITION, nodes_provider=lambda: nodes
    )
    assert ready is True


@pytest.mark.asyncio
async def test_merge_any_fires_on_first_branch():
    ticket = make_ticket()
    merge_any = dict(MERGE_DEF, strategy=MERGE_STRATEGY_ANY)
    nodes = [
        make_node(node_id="b1", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b1"),
        make_node(node_id="b2y", status=NODE_STATUS_PENDING, fork_id="fork", branch_key="b2"),
    ]
    assert await merge.is_merge_ready(
        ticket, merge_any, FORK_WITH_INNER_CONDITION, nodes_provider=lambda: nodes
    ) is True


@pytest.mark.asyncio
async def test_cancel_losing_branches_marks_active_nodes():
    """SPEC §4.2：任一完成 fire 后其余支路的活跃节点标记取消。
    取消用 results 标记 + completed_at 表达，不占用节点状态枚举。"""
    ticket = make_ticket()
    winner = make_node(node_id="b1", status=NODE_STATUS_APPROVED,
                       fork_id="fork", branch_key="b1")
    loser = make_node(node_id="b2y", status=NODE_STATUS_PENDING,
                      fork_id="fork", branch_key="b2")
    in_flight = make_node(node_id="b2z", status=NODE_STATUS_RUNNING,
                          fork_id="fork", branch_key="b2")
    nodes = [winner, loser, in_flight]

    cancelled = await merge.cancel_losing_branches(
        ticket, dict(MERGE_DEF, strategy=MERGE_STRATEGY_ANY),
        FORK_WITH_INNER_CONDITION, nodes_provider=lambda: nodes,
        record=_noop_record,
    )

    assert {n.node_id for n in cancelled} == {"b2y", "b2z"}
    assert loser.results.get(RESULT_KEY_CANCELLED) is True
    assert loser.completed_at is not None
    assert winner.results.get(RESULT_KEY_CANCELLED) is None


@pytest.mark.asyncio
async def test_cancel_leaves_already_finished_nodes_untouched():
    ticket = make_ticket()
    done = make_node(node_id="b1", status=NODE_STATUS_APPROVED,
                     fork_id="fork", branch_key="b1")
    cancelled = await merge.cancel_losing_branches(
        ticket, dict(MERGE_DEF, strategy=MERGE_STRATEGY_ANY),
        FORK_WITH_INNER_CONDITION, nodes_provider=lambda: [done],
        record=_noop_record,
    )
    assert cancelled == []


@pytest.mark.asyncio
async def test_filter_merge_ready_holds_back_unready_merge():
    """汇聚未满足时不激活它——否则汇聚节点会被每条支路各激活一次。"""
    ticket = make_ticket()
    nodes = [
        make_node(node_id="b1", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b1"),
        make_node(node_id="b2y", status=NODE_STATUS_PENDING, fork_id="fork", branch_key="b2"),
    ]
    targets = await merge.filter_merge_ready(
        ticket, ["merge"], FORK_WITH_INNER_CONDITION, nodes_provider=lambda: nodes
    )
    assert targets == []


@pytest.mark.asyncio
async def test_filter_merge_ready_passes_through_non_merge_targets():
    ticket = make_ticket()
    targets = await merge.filter_merge_ready(
        ticket, ["b1", "b2"], FORK_WITH_INNER_CONDITION, nodes_provider=lambda: []
    )
    assert targets == ["b1", "b2"]


@pytest.mark.asyncio
async def test_merge_activates_only_once_when_last_branch_arrives():
    ticket = make_ticket()
    nodes = [
        make_node(node_id="b1", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b1"),
        make_node(node_id="b2y", status=NODE_STATUS_APPROVED, fork_id="fork", branch_key="b2"),
    ]
    targets = await merge.filter_merge_ready(
        ticket, ["merge"], FORK_WITH_INNER_CONDITION, nodes_provider=lambda: nodes
    )
    assert targets == ["merge"]

    # 已存在 merge 实例时不得再激活一次（CAS 语义，Kafka 是 at-least-once）
    nodes.append(make_node(node_id="merge", status=NODE_STATUS_PENDING, fork_id="fork"))
    again = await merge.filter_merge_ready(
        ticket, ["merge"], FORK_WITH_INNER_CONDITION, nodes_provider=lambda: nodes
    )
    assert again == []


async def _noop_record(*args, **kwargs):
    return None
