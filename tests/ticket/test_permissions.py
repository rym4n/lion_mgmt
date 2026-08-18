# coding=utf-8
"""三层权限判定与 available_actions 计算单测（SPEC §6.2 & §7.1）。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from models.ticket import (
    SOURCE_MANUAL,
    SOURCE_PLAYBOOK,
    TICKET_STATUS_COMPLETED,
    TICKET_STATUS_PROCESSING,
    TICKET_STATUS_SUSPENDED,
)
from services.ticket.permissions import (
    compute_available_actions,
    is_ticket_admin,
    is_ticket_creator,
)


@pytest.fixture
def mock_session_admin():
    """模拟管理员 session。"""
    from unittest.mock import MagicMock
    account = MagicMock()
    account.id = "admin-id"
    account.roles = [MagicMock(id="admin-role", permissions=["ticket:all"])]
    return {
        "tenant_id": "test-tenant",
        "sub": "admin-id",
        "account_info": account,
    }


@pytest.fixture
def mock_session_user():
    """模拟普通用户 session。"""
    from unittest.mock import MagicMock
    account = MagicMock()
    account.id = "user-id"
    account.roles = [MagicMock(id="user-role", permissions=["ticket:r", "ticket:u", "ticket:c"])]
    return {
        "tenant_id": "test-tenant",
        "sub": "user-id",
        "account_info": account,
    }


def test_is_ticket_admin_with_ticket_all(mock_session_admin):
    """管理员拥有 ticket:all 权限。"""
    # Mock account_has_permission to return True
    from unittest.mock import patch
    with patch("services.ticket.permissions.account_has_permission", return_value=True):
        assert is_ticket_admin(mock_session_admin) is True


def test_is_ticket_admin_reads_account_permissions_in_current_tenant():
    """ticket:all must be evaluated from the account object and tenant scope."""
    account = SimpleNamespace(
        id="admin-id",
        roles=[
            SimpleNamespace(
                id="tenant-role",
                tenant_id="test-tenant",
                permissions=["ticket:all"],
            )
        ],
    )

    assert is_ticket_admin({"tenant_id": "test-tenant", "account_info": account}) is True


def test_is_ticket_admin_ignores_ticket_all_from_another_tenant():
    """A role from another tenant must not grant the all-tickets view."""
    account = SimpleNamespace(
        id="admin-id",
        roles=[
            SimpleNamespace(
                id="other-tenant-role",
                tenant_id="other-tenant",
                permissions=["ticket:all"],
            )
        ],
    )

    assert is_ticket_admin({"tenant_id": "test-tenant", "account_info": account}) is False


def test_is_ticket_admin_without_ticket_all(mock_session_user):
    """普通用户无 ticket:all 权限。"""
    from unittest.mock import patch
    with patch("services.ticket.permissions.account_has_permission", return_value=False):
        with patch("services.ticket.permissions.is_superadmin", return_value=False):
            assert is_ticket_admin(mock_session_user) is False


def test_is_ticket_creator():
    """判断提单人。"""
    from unittest.mock import MagicMock
    ticket = MagicMock()
    ticket.creator_id = "user-123"

    assert is_ticket_creator(ticket, "user-123") is True
    assert is_ticket_creator(ticket, "user-456") is False
    assert is_ticket_creator(ticket, None) is False


def test_compute_available_actions_for_terminal_ticket(mock_session_user):
    """终态工单的 available_actions 为空集。"""
    from unittest.mock import MagicMock
    ticket = MagicMock()
    ticket.status = TICKET_STATUS_COMPLETED
    ticket.creator_id = "user-id"

    actions = compute_available_actions(ticket, set(), set(), "user-id", mock_session_user)
    assert actions == []


def test_compute_available_actions_for_suspended_ticket_admin(mock_session_admin):
    """异常挂起态只对管理员开放。"""
    from unittest.mock import MagicMock, patch
    ticket = MagicMock()
    ticket.status = TICKET_STATUS_SUSPENDED
    ticket.creator_id = "admin-id"

    with patch("services.ticket.permissions.is_ticket_admin", return_value=True):
        actions = compute_available_actions(ticket, set(), set(), "admin-id", mock_session_admin)
        assert set(actions) == {"retry_resolve", "assign_approver", "force_close"}


def test_compute_available_actions_for_suspended_ticket_user(mock_session_user):
    """异常挂起态普通用户无操作。"""
    from unittest.mock import MagicMock, patch
    ticket = MagicMock()
    ticket.status = TICKET_STATUS_SUSPENDED
    ticket.creator_id = "user-id"

    with patch("services.ticket.permissions.is_ticket_admin", return_value=False):
        actions = compute_available_actions(ticket, set(), set(), "user-id", mock_session_user)
        assert actions == []


def test_compute_available_actions_for_creator_can_withdraw(mock_session_user):
    """提单人可以撤回（非剧本触发）。"""
    from unittest.mock import MagicMock, patch
    ticket = MagicMock()
    ticket.status = TICKET_STATUS_PROCESSING
    ticket.creator_id = "user-id"
    ticket.source = SOURCE_MANUAL

    with patch("services.ticket.permissions.is_ticket_admin", return_value=False):
        with patch("services.ticket.permissions.account_has_permission", return_value=False):
            actions = compute_available_actions(ticket, set(), set(), "user-id", mock_session_user)
            assert "withdraw" in actions
            assert "urge" in actions


def test_compute_available_actions_for_creator_cannot_withdraw_playbook(mock_session_user):
    """剧本触发的工单提单人不能撤回。"""
    from unittest.mock import MagicMock, patch
    ticket = MagicMock()
    ticket.status = TICKET_STATUS_PROCESSING
    ticket.creator_id = "user-id"
    ticket.source = SOURCE_PLAYBOOK

    with patch("services.ticket.permissions.is_ticket_admin", return_value=False):
        with patch("services.ticket.permissions.account_has_permission", return_value=False):
            actions = compute_available_actions(ticket, set(), set(), "user-id", mock_session_user)
            assert "withdraw" not in actions
            assert "urge" not in actions


def test_compute_available_actions_grants_link_from_current_tenant_permission():
    """ticket:u from the current tenant enables the link action."""
    account = SimpleNamespace(
        id="user-id",
        roles=[
            SimpleNamespace(
                id="tenant-role",
                tenant_id="test-tenant",
                permissions=["ticket:u"],
            )
        ],
    )
    session = {
        "tenant_id": "test-tenant",
        "sub": "user-id",
        "account_info": account,
    }
    ticket = MagicMock(
        status=TICKET_STATUS_PROCESSING,
        creator_id="other-user",
        source=SOURCE_MANUAL,
    )

    actions = compute_available_actions(ticket, set(), set(), "user-id", session)

    assert "link" in actions


def test_compute_available_actions_for_approver(mock_session_user):
    """当前审批人可以审批/转派/退回/加签。"""
    from unittest.mock import MagicMock, patch

    ticket = MagicMock()
    ticket.status = TICKET_STATUS_PROCESSING
    ticket.creator_id = "other-user"
    ticket.source = SOURCE_MANUAL

    active_approver_ids = {"user-id"}
    all_approver_ids = {"user-id"}

    with patch("services.ticket.permissions.is_ticket_admin", return_value=False):
        with patch("services.ticket.permissions.account_has_permission", return_value=False):
            actions = compute_available_actions(
                ticket, active_approver_ids, all_approver_ids, "user-id", mock_session_user
            )
            assert "approve" in actions
            assert "reject" in actions
            assert "transfer" in actions
            assert "return" in actions
            assert "add_approver" in actions


def test_compute_available_actions_admin_privileges(mock_session_admin):
    """管理员拥有强制关闭与指派权限。"""
    from unittest.mock import MagicMock, patch

    ticket = MagicMock()
    ticket.status = TICKET_STATUS_PROCESSING
    ticket.creator_id = "other-user"
    ticket.source = SOURCE_MANUAL

    with patch("services.ticket.permissions.is_ticket_admin", return_value=True):
        with patch("services.ticket.permissions.account_has_permission", return_value=True):
            actions = compute_available_actions(ticket, set(), set(), "admin-id", mock_session_admin)
            assert "force_close" in actions
            assert "assign" in actions


# ---------- Task 2: 新增测试暴露两个缺陷 ----------


def test_approver_detection_does_not_depend_on_tortoise_internals(mock_session_user):
    """审批人判定不得依赖 _prefetched_approvers_val 这类内部属性。

    缺陷一: compute_available_actions 依赖 Tortoise 内部属性 _prefetched_approvers_val,
    拿不到就静默返回空列表,导致审批人识别不出来,按钮全不显示。

    计划 1 的教训: 探测失败静默退化,曾导致状态机整体空转且返回值仍是 True。
    """
    from unittest.mock import MagicMock, patch

    ticket = MagicMock()
    ticket.status = TICKET_STATUS_PROCESSING
    ticket.creator_id = "other-user"
    ticket.source = SOURCE_MANUAL

    # 新实现：调用方传入已解析的审批人 ID 集合
    active_approver_ids = {"user-id"}
    all_approver_ids = {"user-id"}

    with patch("services.ticket.permissions.is_ticket_admin", return_value=False):
        with patch("services.ticket.permissions.account_has_permission", return_value=False):
            actions = compute_available_actions(
                ticket, active_approver_ids, all_approver_ids, "user-id", mock_session_user
            )

            # 期望: 审批人应该能执行审批操作
            assert "approve" in actions, "审批人应该能审批,不应依赖 _prefetched_approvers_val"
            assert "reject" in actions
            assert "transfer" in actions


def test_historical_approver_can_comment_and_attach(mock_session_user):
    """历史节点审批人也能评论和上传附件。

    缺陷二: comment/attachment 权限只检查当前审批人,遗漏历史审批人。
    SPEC §7.1 要求: 任一节点审批人(含历史)都能 comment/attachment。
    """
    from unittest.mock import MagicMock, patch

    ticket = MagicMock()
    ticket.status = TICKET_STATUS_PROCESSING
    ticket.creator_id = "creator-id"
    ticket.source = SOURCE_MANUAL

    # user-id 不是提单人,不是当前审批人,但是历史审批人
    active_approver_ids = {"other-approver"}  # 当前活跃节点的审批人
    all_approver_ids = {"user-id", "other-approver"}  # 全部节点(含历史)的审批人

    with patch("services.ticket.permissions.is_ticket_admin", return_value=False):
        with patch("services.ticket.permissions.account_has_permission", return_value=False):
            actions = compute_available_actions(
                ticket, active_approver_ids, all_approver_ids, "user-id", mock_session_user
            )

            # 期望: 历史审批人应该能评论和上传附件
            assert "comment" in actions, "历史审批人应该能评论"
            assert "attachment" in actions, "历史审批人应该能上传附件"
            # 但不能执行审批操作(因为不在 active_approver_ids 中)
            assert "approve" not in actions, "历史审批人不应能审批当前节点"
