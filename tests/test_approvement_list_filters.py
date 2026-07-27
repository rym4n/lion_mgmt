# coding=utf-8
import inspect
from types import SimpleNamespace

import pytest

from services.approvement import views as approve_views


class _FakeQ:
    def __init__(self, **kwargs):
        self.expression = ("leaf", kwargs)

    @classmethod
    def combined(cls, operator, left, right):
        instance = cls()
        instance.expression = (operator, left.expression, right.expression)
        return instance

    def __and__(self, other):
        return self.combined("and", self, other)

    def __or__(self, other):
        return self.combined("or", self, other)


class _EmptyQuery:
    async def count(self):
        return 0


@pytest.mark.asyncio
async def test_approval_keyword_keeps_account_and_tenant_scope(monkeypatch):
    captured_conditions = []

    class _FakeApprovement:
        @staticmethod
        def filter(condition):
            captured_conditions.append(condition.expression)
            return _EmptyQuery()

    async def _expire_pending_approvals(**_kwargs):
        return 0

    monkeypatch.setattr(approve_views, "Q", _FakeQ)
    monkeypatch.setattr(approve_views, "Approvement", _FakeApprovement)
    monkeypatch.setattr(approve_views, "expire_pending_approvals", _expire_pending_approvals)
    monkeypatch.setattr(approve_views, "response_ok", lambda data: data)

    request = SimpleNamespace(
        json={"keyword": "告警", "status": []},
        ctx=SimpleNamespace(
            session={
                "tenant_id": "tenant-1",
                "account_info": SimpleNamespace(id="account-1"),
            }
        ),
    )

    result = await inspect.unwrap(approve_views.all_approvement)(request)

    assert result["total"] == 0
    assert captured_conditions == [
        (
            "and",
            ("leaf", {"approvers": "account-1", "tenant_id": "tenant-1"}),
            (
                "or",
                ("leaf", {"title__icontains": "告警"}),
                ("leaf", {"content__icontains": "告警"}),
            ),
        )
    ]
