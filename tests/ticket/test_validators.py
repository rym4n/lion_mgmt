# coding=utf-8
"""会签/或签规则。SPEC §4.5 六种场景 × 两种签署类型，逐格覆盖。

法定基准 = 当前 approvers 去重后人数，实时计算。现网的
len(results) == len(approvers) 在转派/加签时就崩，本模块替换它。
"""

import pytest

from models.ticket import (
    APPROVE_TYPE_ALL,
    APPROVE_TYPE_ANY,
    NODE_STATUS_APPROVED,
    NODE_STATUS_REJECTED,
)
from services.ticket import validators
from tests.ticket.conftest import make_node

APPROVED = {"result": "approve"}
REJECTED = {"result": "reject"}


# ---------- 或签 ----------

def test_any_passes_on_first_approval():
    outcome = validators.evaluate_node_outcome(
        APPROVE_TYPE_ANY, ["a", "b", "c"], {"a": APPROVED}
    )
    assert outcome == NODE_STATUS_APPROVED


def test_any_rejects_on_first_rejection():
    outcome = validators.evaluate_node_outcome(
        APPROVE_TYPE_ANY, ["a", "b"], {"a": REJECTED}
    )
    assert outcome == NODE_STATUS_REJECTED


def test_any_pending_when_nobody_acted():
    assert validators.evaluate_node_outcome(APPROVE_TYPE_ANY, ["a", "b"], {}) is None


# ---------- 会签 ----------

def test_all_requires_every_current_approver():
    assert validators.evaluate_node_outcome(
        APPROVE_TYPE_ALL, ["a", "b", "c"], {"a": APPROVED, "b": APPROVED}
    ) is None
    assert validators.evaluate_node_outcome(
        APPROVE_TYPE_ALL, ["a", "b", "c"],
        {"a": APPROVED, "b": APPROVED, "c": APPROVED},
    ) == NODE_STATUS_APPROVED


def test_all_rejects_immediately_without_collecting_rest():
    """SPEC §4.5：会签任一人拒绝即整体拒绝，不继续收集。"""
    outcome = validators.evaluate_node_outcome(
        APPROVE_TYPE_ALL, ["a", "b", "c"], {"a": APPROVED, "b": REJECTED}
    )
    assert outcome == NODE_STATUS_REJECTED


def test_all_quorum_is_deduplicated_headcount():
    """转派给已是审批人的人 → 去重后法定人数 -1，此时可能立即达成。"""
    outcome = validators.evaluate_node_outcome(
        APPROVE_TYPE_ALL, ["a", "b", "b"], {"a": APPROVED, "b": APPROVED}
    )
    assert outcome == NODE_STATUS_APPROVED


def test_all_ignores_results_from_non_approvers():
    """不变量 results ⊆ approvers：被转派走的人的旧记录不得充数。"""
    outcome = validators.evaluate_node_outcome(
        APPROVE_TYPE_ALL, ["a", "b"], {"a": APPROVED, "ghost": APPROVED}
    )
    assert outcome is None


def test_empty_approver_set_never_silently_passes():
    """全部审批人失效时不得视为通过——静默通过审批节点是安全漏洞。"""
    assert validators.evaluate_node_outcome(APPROVE_TYPE_ALL, [], {}) is None
    assert validators.evaluate_node_outcome(APPROVE_TYPE_ANY, [], {}) is None


# ---------- results 清理（维持 results ⊆ approvers）----------

def test_purge_stale_results_drops_transferred_away_records():
    """SPEC §4.5：转派后被转派人的 results 记录作废。"""
    cleaned = validators.purge_stale_results(
        ["a", "c"], {"a": APPROVED, "b": APPROVED}
    )
    assert cleaned == {"a": APPROVED}


def test_purge_stale_results_is_pure():
    original = {"a": APPROVED, "b": APPROVED}
    validators.purge_stale_results(["a"], original)
    assert set(original) == {"a", "b"}


# ---------- 加签 ----------

@pytest.mark.asyncio
async def test_add_approver_rejected_when_quorum_already_reached():
    """SPEC §4.5：会签已达成则节点已终态，禁止加签（错误码 4240）。"""
    from core.error_code import ErrorCode
    from libs.error_code.exception import CustomError

    node = make_node(
        approve_type=APPROVE_TYPE_ALL, approver_ids=("a", "b"),
        results={"a": APPROVED, "b": APPROVED},
    )
    with pytest.raises(CustomError) as exc:
        await validators.validate_approver_change(node, "add_approver", ["c"])
    assert exc.value.enum is ErrorCode.TICKET_QUORUM_REACHED


@pytest.mark.asyncio
async def test_add_approver_raises_quorum_for_countersign():
    """会签加签 → 法定人数 +1，原本 2/2 达成变 2/3 未达成。"""
    node = make_node(
        approve_type=APPROVE_TYPE_ALL, approver_ids=("a", "b"),
        results={"a": APPROVED},
    )
    after = await validators.validate_approver_change(
        node, "add_approver", ["c"], resolve_accounts=lambda ids: ids
    )
    assert sorted(after) == ["a", "b", "c"]
    assert validators.evaluate_node_outcome(APPROVE_TYPE_ALL, after, node.results) is None


@pytest.mark.asyncio
async def test_add_approver_deduplicates_existing_member():
    node = make_node(approve_type=APPROVE_TYPE_ALL, approver_ids=("a", "b"))
    after = await validators.validate_approver_change(
        node, "add_approver", ["b"], resolve_accounts=lambda ids: ids
    )
    assert sorted(after) == ["a", "b"]


# ---------- 转派 ----------

@pytest.mark.asyncio
async def test_transfer_rejects_missing_actor_id():
    from core.error_code import ErrorCode
    from libs.error_code.exception import CustomError

    node = make_node(approve_type=APPROVE_TYPE_ALL, approver_ids=("a", "b"))
    with pytest.raises(CustomError) as exc:
        await validators.validate_approver_change(
            node, "transfer", ["c"], resolve_accounts=lambda ids: ids
        )
    assert exc.value.enum is ErrorCode.PARAM_ERROR


@pytest.mark.asyncio
async def test_transfer_replaces_actor_keeping_headcount():
    node = make_node(approve_type=APPROVE_TYPE_ALL, approver_ids=("a", "b"))
    after = await validators.validate_approver_change(
        node, "transfer", ["c"], actor_id="a", resolve_accounts=lambda ids: ids
    )
    assert sorted(after) == ["b", "c"]


@pytest.mark.asyncio
async def test_transfer_to_existing_approver_shrinks_quorum():
    """SPEC §4.5：转派给已是审批人的人 → 去重，法定人数 -1。"""
    node = make_node(
        approve_type=APPROVE_TYPE_ALL, approver_ids=("a", "b"),
        results={"b": APPROVED},
    )
    after = await validators.validate_approver_change(
        node, "transfer", ["b"], actor_id="a", resolve_accounts=lambda ids: ids
    )
    assert after == ["b"]
    # 去重后仅剩 b，且 b 已通过 → 立即达成
    cleaned = validators.purge_stale_results(after, node.results)
    assert validators.evaluate_node_outcome(
        APPROVE_TYPE_ALL, after, cleaned
    ) == NODE_STATUS_APPROVED


@pytest.mark.asyncio
async def test_transfer_rejects_when_target_not_current_tenant():
    from core.error_code import ErrorCode
    from libs.error_code.exception import CustomError

    node = make_node(approver_ids=("a",), tenant_id="tenant-1")
    with pytest.raises(CustomError) as exc:
        await validators.validate_approver_change(
            node, "transfer", ["x"], actor_id="a",
            resolve_accounts=lambda ids: [],   # 解析不到 → 无效
        )
    assert exc.value.enum is ErrorCode.TICKET_APPROVER_INVALID


@pytest.mark.asyncio
async def test_change_rejected_on_non_pending_node():
    """节点已终态时任何审批人变更都非法（错误码 4211）。"""
    from core.error_code import ErrorCode
    from libs.error_code.exception import CustomError

    node = make_node(status=NODE_STATUS_APPROVED, approver_ids=("a",))
    with pytest.raises(CustomError) as exc:
        await validators.validate_approver_change(node, "transfer", ["b"], actor_id="a")
    assert exc.value.enum is ErrorCode.TICKET_NODE_NOT_ACTIVE


@pytest.mark.asyncio
async def test_empty_target_list_rejected():
    from libs.error_code.exception import CustomError

    node = make_node(approver_ids=("a",))
    with pytest.raises(CustomError):
        await validators.validate_approver_change(node, "transfer", [], actor_id="a")


# ---------- 审批人失效 ----------

def test_disabled_approvers_excluded_from_quorum_for_any():
    """或签：剔除失效人，剩余人仍可推进。"""
    effective = validators.effective_approver_ids(
        [("a", True), ("b", False)]
    )
    assert effective == ["a"]
    assert validators.evaluate_node_outcome(
        APPROVE_TYPE_ANY, effective, {"a": APPROVED}
    ) == NODE_STATUS_APPROVED


def test_all_approvers_disabled_yields_empty_and_never_passes():
    """SPEC §4.5：会签全部失效 → 转 5异常，绝不视为通过。"""
    effective = validators.effective_approver_ids([("a", False), ("b", False)])
    assert effective == []
    assert validators.evaluate_node_outcome(APPROVE_TYPE_ALL, effective, {}) is None
