# coding=utf-8

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from services.approvement import views as approve_views


class _FakeQuery:
    def __init__(self, records):
        self.records = records

    async def all(self):
        return self.records

    async def update(self, **kwargs):
        for record in self.records:
            for key, value in kwargs.items():
                setattr(record, key, value)
        return len(self.records)


class _FakeApprovement:
    records = []
    filter_calls = []

    @classmethod
    def filter(cls, *_args, **kwargs):
        cls.filter_calls.append(kwargs)
        records = cls.records
        if "id__in" in kwargs:
            ids = set(kwargs["id__in"])
            records = [record for record in records if record.id in ids]
        else:
            now = datetime.now()
            records = [record for record in records if record.expire_time < now]
        if "status" in kwargs:
            records = [record for record in records if record.status == kwargs["status"]]
        return _FakeQuery(records)


class _FakeCommandApproval:
    records = []

    @classmethod
    def filter(cls, **kwargs):
        ids = set(kwargs.get("id__in") or [])
        return _FakeQuery(
            [
                record
                for record in cls.records
                if record.id in ids and record.status == kwargs.get("status")
            ]
        )


class _FakeRunlog:
    records = []
    filter_calls = []

    @classmethod
    def filter(cls, *_args, **_kwargs):
        cls.filter_calls.append((_args, _kwargs))
        return _FakeQuery([record for record in cls.records if record.status == 2])


@pytest.mark.asyncio
async def test_expire_pending_approvals_updates_approval_inbox_and_runlog(monkeypatch):
    approval = SimpleNamespace(
        id="approval-1",
        tenant_id="tenant-1",
        status=0,
        run_id="run-1",
        audit_node="node-1",
        expire_time=datetime.now() - timedelta(seconds=1),
        params={"approval_kind": "agent", "command_approval_id": "command-approval-1"},
    )
    already_expired = SimpleNamespace(
        id="approval-2",
        tenant_id="tenant-1",
        status=3,
        run_id="run-2",
        audit_node="node-2",
        expire_time=datetime.now() - timedelta(days=1),
        params={},
    )
    command_approval = SimpleNamespace(id="command-approval-1", status="pending")
    runlog = SimpleNamespace(run_id="run-1", node_id="node-1", status=2, result="待审批")
    _FakeApprovement.records = [approval, already_expired]
    _FakeApprovement.filter_calls = []
    _FakeCommandApproval.records = [command_approval]
    _FakeRunlog.records = [runlog]
    _FakeRunlog.filter_calls = []

    monkeypatch.setattr(approve_views, "Approvement", _FakeApprovement)
    monkeypatch.setattr(approve_views, "CommandApproval", _FakeCommandApproval)
    monkeypatch.setattr(approve_views, "Runlog", _FakeRunlog)

    expired_count = await approve_views.expire_pending_approvals(tenant_id="tenant-1", run_id="run-1")

    assert expired_count == 1
    assert approval.status == 3
    assert command_approval.status == "expired"
    assert runlog.status == 3
    assert runlog.result == "审核已超时"
    assert already_expired.status == 3
    assert _FakeApprovement.filter_calls[0] == {"status": 0, "deleted": False}
    assert len(_FakeRunlog.filter_calls) == 1
