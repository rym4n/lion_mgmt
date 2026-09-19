# coding=utf-8
"""工单测试共享夹具。

本仓测试不连数据库（SPEC §9.2）：模型引用一律 monkeypatch 成假对象，
行锁/claim 互斥等依赖真实 DB 语义的项走 192.168.12.254 UAT。
"""

from datetime import datetime
import importlib
import sys
from types import SimpleNamespace

import pytest


# Some earlier tests replace these entries in ``sys.modules`` with lightweight
# fakes and leave them there.  Capture the real objects while ticket tests are
# collected so each ticket test can restore the exact identities its imports
# were resolved against.
_REAL_MODULE_NAMES = (
    "models",
    "models.command_center",
    "models.ticket",
    "core",
    "core.error_code",
    "libs",
    "libs.error_code",
    "libs.error_code.exception",
    "services",
    "services.ticket",
    "services.ticket.auto_action",
    "services.ticket.callbacks",
    "services.ticket.engine",
    "services.ticket.manager",
    "services.ticket.merge",
    "services.ticket.playbook_bridge",
    "services.ticket.sla_scheduler",
    "services.ticket.ticket_no",
    "services.ticket.validators",
    "services.ticket.websocket",
)
_REAL_MODULES = {
    name: importlib.import_module(name) for name in _REAL_MODULE_NAMES
}


@pytest.fixture(autouse=True)
def _restore_real_modules():
    """Undo module replacement left behind by unrelated test modules."""
    for name, module in _REAL_MODULES.items():
        sys.modules[name] = module

    # ``from package import child`` consults the parent attribute as well as
    # sys.modules, so restore that link when a fake parent was installed.
    for name, module in _REAL_MODULES.items():
        parent_name, _, child_name = name.rpartition(".")
        if parent_name in _REAL_MODULES:
            setattr(_REAL_MODULES[parent_name], child_name, module)

    yield


class FakeQuery:
    """模拟 Tortoise QuerySet 的链式调用。链上方法返回 self，终结方法返回数据。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.update_calls = []
        self.using_db_calls = []

    # --- 链式（返回 self）---
    def filter(self, *args, **kwargs):
        # *args 用于接收 Q(...) 表达式（如 ticket_flow_ids_for_user 的
        # Q(users_id=...) | Q(roles_id__in=...) 链式过滤），FakeQuery 不做真实
        # 过滤逻辑，args 直接忽略即可。
        return self

    def using_db(self, conn):
        self.using_db_calls.append(conn)
        return self

    def select_for_update(self, *args, **kwargs):
        return self

    def order_by(self, *args):
        return self

    def prefetch_related(self, *args):
        return self

    def limit(self, _n):
        return self

    # --- 终结 ---
    async def all(self):
        return self.rows

    async def first(self):
        return self.rows[0] if self.rows else None

    async def count(self):
        return len(self.rows)

    async def exists(self):
        return bool(self.rows)

    async def update(self, **kwargs):
        self.update_calls.append(kwargs)
        for row in self.rows:
            for key, value in kwargs.items():
                setattr(row, key, value)
        return len(self.rows)


class FakeM2M:
    """模拟 ManyToMany 管理器。approvers 是 M2M，测试要能读写它。"""

    def __init__(self, members=None):
        self.members = list(members or [])
        self.added = []
        self.removed = []
        self.added_using_db = []
        self.removed_using_db = []

    async def all(self):
        return self.members

    async def add(self, *objs, using_db=None):
        self.added.extend(objs)
        self.added_using_db.append(using_db)
        self.members.extend(o for o in objs if o not in self.members)

    async def remove(self, *objs, using_db=None):
        self.removed.extend(objs)
        self.removed_using_db.append(using_db)
        self.members = [m for m in self.members if m not in objs]


def make_account(account_id, enabled=True, tenant_id="tenant-1"):
    return SimpleNamespace(
        id=account_id, username=f"user-{account_id}", display_name=f"用户{account_id}",
        enabled=enabled, deleted=False, tenant_id=tenant_id,
    )


def make_node(
    node_id="n1", status=0, approve_type=1, approver_ids=(), results=None,
    node_type="approve", ticket_id="t1", fork_id=None, branch_key=None,
    deadline=None, entered_at=None, tenant_id="tenant-1",
):
    """造一个节点实例桩。approvers 用 FakeM2M，saved 记录 save 调用。"""
    node = SimpleNamespace(
        id=f"inst-{node_id}", node_id=node_id, node_name=node_id, node_type=node_type,
        status=status, approve_type=approve_type, results=dict(results or {}),
        form_data={}, deadline=deadline, entered_at=entered_at, completed_at=None,
        fork_id=fork_id, branch_key=branch_key, auto_run_id=None,
        ticket_id=ticket_id, tenant_id=tenant_id, deleted=False, saved=[],
        saved_using_db=[],
    )
    node.approvers = FakeM2M([make_account(i, tenant_id=tenant_id) for i in approver_ids])

    async def _save(update_fields=None, using_db=None):
        node.saved.append(tuple(update_fields or ()))
        node.saved_using_db.append(using_db)

    node.save = _save
    return node


def make_ticket(
    ticket_id="t1", status=0, source=0, tenant_id="tenant-1",
    ctime=None, sla_respond_at=None, sla_resolve_at=None, suspended_at=None,
    flow_json=None, form_data=None,
):
    ticket = SimpleNamespace(
        id=ticket_id, ticket_no="CHG-20260810-0001", title="测试工单",
        status=status, source=source, tenant_id=tenant_id,
        deleted=False, ctime=ctime or datetime(2026, 8, 10, 9, 0, 0),
        sla_respond_at=sla_respond_at, sla_resolve_at=sla_resolve_at,
        closed_at=None, suspended_at=suspended_at,
        sweep_owner=None, sweep_expires_at=None,
        form_data=dict(form_data or {}), creator_id="acc-creator",
        ticket_type_id="type-1", flow_version_id="ver-1",
        origin_run_id=None, origin_node_id=None, origin_flow_id=None,
        origin_params=None, callback_token=None, saved=[], saved_using_db=[],
    )

    async def _save(update_fields=None, using_db=None):
        ticket.saved.append(tuple(update_fields or ()))
        ticket.saved_using_db.append(using_db)

    ticket.save = _save
    ticket._flow_json = flow_json or {}
    return ticket


@pytest.fixture
def accounts():
    return {i: make_account(i) for i in ("a", "b", "c", "d")}
