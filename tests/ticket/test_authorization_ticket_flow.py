# coding=utf-8
"""工单流程级权限单测（SPEC §6.2 三层权限判定中的第二层）。

覆盖 libs/security/authorization.py 的 4 个函数：
1. ticket_flow_permission 装饰器 —— 校验 GET 请求（request.json 为 None）不崩
2. check_ticket_flow_permission —— 超管豁免、角色遍历
3. ticket_flow_ids_for_user —— role_ids 为空时不构造非法 Q
4. 跨租户、无记录、角色授权、超管全通 四类场景
"""

from types import SimpleNamespace

import pytest

from libs.security.authorization import (
    SUPERADMIN_ROLE_ID,
    check_ticket_flow_permission,
    ticket_flow_ids_for_user,
    ticket_flow_permission,
)
from tests.ticket.conftest import FakeQuery, make_account


def make_role(role_id, permissions=None, tenant_id="tenant-1"):
    return SimpleNamespace(id=role_id, permissions=list(permissions or []), tenant_id=tenant_id)


def make_flow_permission_record(flow_id, tenant_id, permissions, users_id=None, roles_id=None):
    return SimpleNamespace(
        flow_id=flow_id, tenant_id=tenant_id, permissions=list(permissions),
        users_id=users_id, roles_id=roles_id, deleted=False,
    )


def make_user(user_id="user-1", roles=None):
    account = make_account(user_id)
    account.roles = list(roles or [])
    return account


class FakeRequest:
    """模拟 Sanic Request：json 可能为 None（GET 请求），args 是 query 参数。"""

    def __init__(self, json=None, args=None, session=None):
        self.json = json
        self.args = args or {}
        self.ctx = SimpleNamespace(session=session or {})


# ---------- 1. ticket_flow_permission 装饰器：GET 请求不崩 ----------


@pytest.mark.asyncio
async def test_ticket_flow_permission_get_request_does_not_crash(monkeypatch):
    """GET 请求时 request.json 为 None，装饰器不得抛 AttributeError。

    缺陷: 旧实现直接 request.json.get("flow_id")，GET 端点上 request.json 为 None 时崩溃。
    修复: 改为 (request.json or {})，并支持从 request.args / 路径参数取 flow_id。
    """
    user = make_user("user-1")

    async def fake_check(request, u, flow_id, required_permissions):
        assert flow_id == "flow-1"
        return True

    monkeypatch.setattr(
        "libs.security.authorization.check_ticket_flow_permission", fake_check
    )

    @ticket_flow_permission("r")
    async def handler(request, flow_id=None):
        return "ok"

    # GET 请求：json 为 None，flow_id 从路径参数（kwargs）传入
    request = FakeRequest(
        json=None, args={}, session={"tenant_id": "tenant-1", "account_info": user}
    )
    result = await handler(request, flow_id="flow-1")
    assert result == "ok"


@pytest.mark.asyncio
async def test_ticket_flow_permission_get_request_flow_id_from_query(monkeypatch):
    """GET 请求 flow_id 从 query 参数（request.args）取。"""
    user = make_user("user-1")

    async def fake_check(request, u, flow_id, required_permissions):
        assert flow_id == "flow-query"
        return True

    monkeypatch.setattr(
        "libs.security.authorization.check_ticket_flow_permission", fake_check
    )

    @ticket_flow_permission("r")
    async def handler(request):
        return "ok"

    request = FakeRequest(
        json=None,
        args={"flow_id": "flow-query"},
        session={"tenant_id": "tenant-1", "account_info": user},
    )
    result = await handler(request)
    assert result == "ok"


@pytest.mark.asyncio
async def test_ticket_flow_permission_post_request_still_works(monkeypatch):
    """POST 请求（request.json 有值）行为不变。"""
    user = make_user("user-1")

    async def fake_check(request, u, flow_id, required_permissions):
        assert flow_id == "flow-body"
        return True

    monkeypatch.setattr(
        "libs.security.authorization.check_ticket_flow_permission", fake_check
    )

    @ticket_flow_permission("u")
    async def handler(request):
        return "ok"

    request = FakeRequest(
        json={"flow_id": "flow-body"},
        session={"tenant_id": "tenant-1", "account_info": user},
    )
    result = await handler(request)
    assert result == "ok"


# ---------- 2 & 4. check_ticket_flow_permission：四类场景 ----------


@pytest.mark.asyncio
async def test_check_ticket_flow_permission_superadmin_bypass(monkeypatch):
    """超管全通：即使无任何权限记录也放行。"""
    superadmin_role = make_role(SUPERADMIN_ROLE_ID, tenant_id="tenant-1")
    user = make_user("admin-1", roles=[superadmin_role])

    def mock_filter(**kw):
        # 超管应在查记录前就返回 True，不应触发查询
        raise AssertionError("超管豁免不应查询 TicketFlowPermission")

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    request = FakeRequest(session={"tenant_id": "tenant-1"})
    result = await check_ticket_flow_permission(request, user, "flow-1", ("r",))
    assert result is True


@pytest.mark.asyncio
async def test_check_ticket_flow_permission_denies_without_record(monkeypatch):
    """无记录时拒绝。"""
    user = make_user("user-1", roles=[make_role("role-1")])

    def mock_filter(**kw):
        return FakeQuery([])

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    request = FakeRequest(session={"tenant_id": "tenant-1"})
    result = await check_ticket_flow_permission(request, user, "flow-1", ("r",))
    assert result is False


@pytest.mark.asyncio
async def test_check_ticket_flow_permission_cross_tenant_invisible(monkeypatch):
    """跨租户流程不可见：记录属于另一个租户时不命中。"""
    user = make_user("user-1", roles=[])

    other_tenant_record = make_flow_permission_record(
        "flow-1", "other-tenant", ["r"], users_id="user-1"
    )

    def mock_filter(**kw):
        # 模拟真实 ORM：按 tenant_id 过滤，跨租户记录不会出现在结果里
        if kw.get("tenant_id") != other_tenant_record.tenant_id:
            return FakeQuery([])
        return FakeQuery([other_tenant_record])

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    request = FakeRequest(session={"tenant_id": "tenant-1"})
    result = await check_ticket_flow_permission(request, user, "flow-1", ("r",))
    assert result is False


@pytest.mark.asyncio
async def test_check_ticket_flow_permission_user_direct_grant(monkeypatch):
    """用户直授权限生效。"""
    user = make_user("user-1", roles=[])

    record = make_flow_permission_record("flow-1", "tenant-1", ["r", "u"], users_id="user-1")

    def mock_filter(**kw):
        if kw.get("users_id") == "user-1":
            return FakeQuery([record])
        return FakeQuery([])

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    request = FakeRequest(session={"tenant_id": "tenant-1"})
    result = await check_ticket_flow_permission(request, user, "flow-1", ("r",))
    assert result is True


@pytest.mark.asyncio
async def test_check_ticket_flow_permission_role_grant(monkeypatch):
    """角色授权生效：用户无直授权限，但所属角色被授权。"""
    role = make_role("role-1")
    user = make_user("user-1", roles=[role])

    record = make_flow_permission_record("flow-1", "tenant-1", ["r"], roles_id="role-1")

    def mock_filter(**kw):
        if kw.get("users_id") == "user-1":
            return FakeQuery([])
        if kw.get("roles_id") == "role-1":
            return FakeQuery([record])
        return FakeQuery([])

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    request = FakeRequest(session={"tenant_id": "tenant-1"})
    result = await check_ticket_flow_permission(request, user, "flow-1", ("r",))
    assert result is True


@pytest.mark.asyncio
async def test_check_ticket_flow_permission_uses_user_roles_attribute(monkeypatch):
    """角色遍历用 user.roles（非 session 中的其他字段）。"""
    role = make_role("role-x")
    user = make_user("user-1", roles=[role])

    seen_role_ids = []

    def mock_filter(**kw):
        if "roles_id" in kw:
            seen_role_ids.append(kw["roles_id"])
        return FakeQuery([])

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    request = FakeRequest(session={"tenant_id": "tenant-1"})
    await check_ticket_flow_permission(request, user, "flow-1", ("r",))
    assert seen_role_ids == ["role-x"]


# ---------- 3. ticket_flow_ids_for_user：role_ids 为空时不构造非法 Q ----------


@pytest.mark.asyncio
async def test_ticket_flow_ids_for_user_empty_roles_does_not_crash(monkeypatch):
    """role_ids 为空时不得构造非法 Q(roles_id__in=[])。"""
    user = make_user("user-1", roles=[])  # 无任何角色

    record = make_flow_permission_record("flow-1", "tenant-1", ["r"], users_id="user-1")

    captured_filters = []

    def mock_filter(*args, **kw):
        captured_filters.append((args, kw))
        return FakeQuery([record])

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    result = await ticket_flow_ids_for_user(user, "tenant-1", permission="r")
    assert result == {"flow-1"}


@pytest.mark.asyncio
async def test_ticket_flow_ids_for_user_superadmin_returns_all_flows(monkeypatch):
    """超管返回租户下全部流程 id。"""
    superadmin_role = make_role(SUPERADMIN_ROLE_ID, tenant_id="tenant-1")
    user = make_user("admin-1", roles=[superadmin_role])

    flow1 = SimpleNamespace(id="flow-1")
    flow2 = SimpleNamespace(id="flow-2")

    def mock_filter(**kw):
        return FakeQuery([flow1, flow2])

    monkeypatch.setattr("models.ticket.TicketFlow.filter", mock_filter)

    result = await ticket_flow_ids_for_user(user, "tenant-1")
    assert result == {"flow-1", "flow-2"}


@pytest.mark.asyncio
async def test_ticket_flow_ids_for_user_filters_by_permission(monkeypatch):
    """只返回具有指定权限（如 'r'）的流程 id，不含无权限的记录。"""
    user = make_user("user-1", roles=[])

    granted = make_flow_permission_record("flow-1", "tenant-1", ["r"], users_id="user-1")
    not_granted = make_flow_permission_record("flow-2", "tenant-1", ["u"], users_id="user-1")

    def mock_filter(**kw):
        return FakeQuery([granted, not_granted])

    monkeypatch.setattr("models.ticket.TicketFlowPermission.filter", mock_filter)

    result = await ticket_flow_ids_for_user(user, "tenant-1", permission="r")
    assert result == {"flow-1"}
