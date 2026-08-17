# coding=utf-8

import inspect
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.approvement import compat
from services.approvement import views as approve_views


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


def test_waiting_node_type_branch_keeps_legacy_and_ticket_semantics_separate():
    log = {"users": "account-1"}

    assert compat.is_legacy_approval_event(log, {"type": "approval-node"}) is True
    assert compat.is_legacy_approval_event(log, {"type": "ticket-node"}) is False
    assert compat.is_legacy_approval_event(log, None) is True
    assert compat.is_legacy_approval_event(
        {"ticket_flow_id": "flow-1"}, None
    ) is False


def test_decision_status_only_completes_countersign_after_all_approvers():
    approvers = {"account-1", "account-2"}

    assert approve_views._decision_status(1, True, {"account-1": {}}, approvers) == 1
    assert approve_views._decision_status(2, True, {"account-1": {}}, approvers) == 0
    assert approve_views._decision_status(
        2,
        True,
        {"account-1": {}, "account-2": {}},
        approvers,
    ) == 1
    assert approve_views._decision_status(2, False, {"account-1": {}}, approvers) == 2


@pytest.mark.asyncio
async def test_legacy_approval_creation_is_idempotent(monkeypatch):
    records = []
    links = set()
    lock_calls = []
    account = SimpleNamespace(id="account-1")

    class _Connection:
        async def execute_query(self, sql, params):
            if "pg_advisory_xact_lock" in sql:
                lock_calls.append(tuple(params))
            elif "INSERT INTO approve_accounts" in sql:
                links.add((params[0], params[1]))

        async def execute_query_dict(self, _sql, params):
            return [
                {"account_id": account_id}
                for approvement_id, account_id in links
                if approvement_id == params[0]
            ]

    class _Query:
        def using_db(self, _connection):
            return self

        def order_by(self, *_args):
            return self

        async def first(self):
            return records[-1] if records else None

    class _Approvement:
        @classmethod
        def filter(cls, **_kwargs):
            return _Query()

        @classmethod
        async def create(cls, **kwargs):
            record = SimpleNamespace(id="approval-1", **kwargs)
            records.append(record)
            return record

    connection = _Connection()
    monkeypatch.setattr(compat, "in_transaction", lambda: _Transaction(connection))
    monkeypatch.setattr(compat, "Approvement", _Approvement)

    async def _resolve(*_args, **_kwargs):
        return [account]

    monkeypatch.setattr(compat, "_resolve_approvers", _resolve)
    log = {
        "tenant_id": "tenant-1",
        "run_id": "run-1",
        "audit_app": "node-1",
        "flow_id": "flow-1",
        "node_name": "人工审批",
        "content": "确认执行",
        "expire_time": "2026-08-17T00:00:00",
        "created_at": "2026-08-16T23:00:00",
        "approve_type": "1",
        "users": "account-1",
    }

    first, first_created, first_notifications = await compat.ensure_legacy_approvement(
        log, {"type": "approval-node", "properties": {}}
    )
    second, second_created, second_notifications = await compat.ensure_legacy_approvement(
        log, {"type": "approval-node", "properties": {}}
    )

    assert first is second
    assert first_created is True
    assert second_created is False
    assert first_notifications == [account]
    assert second_notifications == [account]
    assert len(records) == 1
    assert links == {("approval-1", "account-1")}
    assert lock_calls == [
        ("tenant-1", "run-1:node-1"),
        ("tenant-1", "run-1:node-1"),
    ]


class _ApprovalQuery:
    def __init__(self, approval, captured, filters):
        self.approval = approval
        self.captured = captured
        self.captured.append(filters)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self

    def prefetch_related(self, *_args):
        return self

    async def first(self):
        return self.approval


class _Approval:
    def __init__(self, *, approve_type=2, results=None):
        self.id = "approval-1"
        self.status = 0
        self.results = results or {}
        self.approve_type = approve_type
        self.params = []
        self.run_id = "run-1"
        self.flow_id = "flow-1"
        self.audit_node = "node-1"
        self.tenant_id = "tenant-1"
        self.callback_token = "callback-token"
        self.approvers = [SimpleNamespace(id="account-1")]
        self.saved = []

    async def save(self, **kwargs):
        self.saved.append(kwargs)


def _request(*, account_id="account-1", tenant_id="tenant-1", body=None):
    return SimpleNamespace(
        method="POST",
        json=body or {},
        args={},
        ctx=SimpleNamespace(
            session={
                "tenant_id": tenant_id,
                "account_info": SimpleNamespace(
                    id=account_id,
                    username=account_id,
                    display_name=account_id,
                ),
            }
        ),
    )


@pytest.mark.asyncio
async def test_update_rejects_non_approver_and_keeps_tenant_scope(monkeypatch):
    approval = _Approval()
    captured = []

    class _Approvement:
        @classmethod
        def filter(cls, **filters):
            return _ApprovalQuery(approval, captured, filters)

    connection = SimpleNamespace()
    monkeypatch.setattr(
        approve_views, "in_transaction", lambda: _Transaction(connection)
    )
    monkeypatch.setattr(approve_views, "Approvement", _Approvement)

    async def _approvers(*_args):
        return {"account-2"}

    monkeypatch.setattr(approve_views, "_approver_ids", _approvers)
    request = _request(
        account_id="account-1",
        tenant_id="tenant-1",
        body={"id": "approval-1", "agree": True, "remark": "ok"},
    )

    with pytest.raises(CustomError) as exc_info:
        await inspect.unwrap(approve_views.update_approvement)(request)

    assert exc_info.value.enum is ErrorCode.NOT_FOUND
    assert captured == [
        {
            "id": "approval-1",
            "tenant_id": "tenant-1",
            "deleted": False,
            "flow_id__isnull": False,
        }
    ]


@pytest.mark.asyncio
async def test_countersign_partial_vote_does_not_resume_but_final_vote_does(
    monkeypatch,
):
    published = []
    connection = SimpleNamespace()
    monkeypatch.setattr(
        approve_views, "in_transaction", lambda: _Transaction(connection)
    )
    monkeypatch.setattr(approve_views, "response_ok", lambda data: data)

    async def _approvers(*_args):
        return {"account-1", "account-2"}

    async def _publish(topic, message):
        published.append((topic, message))

    monkeypatch.setattr(approve_views, "_approver_ids", _approvers)
    monkeypatch.setattr(approve_views, "aproduce", _publish)

    partial = _Approval(approve_type=2)

    class _PartialApprovement:
        @classmethod
        def filter(cls, **filters):
            return _ApprovalQuery(partial, [], filters)

    monkeypatch.setattr(approve_views, "Approvement", _PartialApprovement)
    await inspect.unwrap(approve_views.update_approvement)(
        _request(body={"id": "approval-1", "agree": True, "remark": "first"})
    )

    assert partial.status == 0
    assert published == []

    final = _Approval(approve_type=2, results={"account-2": {"agree": True}})

    class _FinalApprovement:
        @classmethod
        def filter(cls, **filters):
            return _ApprovalQuery(final, [], filters)

    monkeypatch.setattr(approve_views, "Approvement", _FinalApprovement)
    await inspect.unwrap(approve_views.update_approvement)(
        _request(body={"id": "approval-1", "agree": True, "remark": "final"})
    )

    assert final.status == 1
    assert len(published) == 1
    assert published[0][1]["run_args"]["approval_status"] == 1
    assert published[0][1]["run_args"]["executor"] == "account-1"


@pytest.mark.asyncio
async def test_callback_consumes_token_and_resumes_playbook(monkeypatch):
    approval = _Approval(approve_type=1)
    captured = []
    published = []

    class _Approvement:
        @classmethod
        def filter(cls, **filters):
            return _ApprovalQuery(approval, captured, filters)

    async def _publish(topic, message):
        published.append((topic, message))

    monkeypatch.setattr(
        approve_views,
        "in_transaction",
        lambda: _Transaction(SimpleNamespace()),
    )
    monkeypatch.setattr(approve_views, "Approvement", _Approvement)
    monkeypatch.setattr(approve_views, "aproduce", _publish)
    monkeypatch.setattr(approve_views, "response_ok", lambda data: data)
    request = SimpleNamespace(
        method="GET",
        json=None,
        args={"token": "callback-token", "agree": "true", "remark": "ok"},
    )

    result = await inspect.unwrap(approve_views.update_approvement_callback)(request)

    assert result == "success"
    assert approval.status == 1
    assert approval.callback_token == ""
    assert captured == [
        {
            "callback_token": "callback-token",
            "deleted": False,
            "flow_id__isnull": False,
        }
    ]
    assert published[0][1]["run_args"]["executor"] == "account-1"
