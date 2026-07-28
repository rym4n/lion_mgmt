# coding=utf-8
import inspect
from types import SimpleNamespace

import pytest

from libs.error_code.exception import CustomError
from services.user import views as user_views


class _TargetAccount:
    def __init__(self, *, account_id="admin-id", username="admin"):
        self.id = account_id
        self.username = username
        self.roles = SimpleNamespace(remove=self._mutate, add=self._mutate)
        self.role_mutations = 0
        self.related_fetches = 0
        self.saved = 0

    async def _mutate(self, *_roles):
        self.role_mutations += 1

    async def fetch_related(self, *_names):
        self.related_fetches += 1

    async def save(self, **_kwargs):
        self.saved += 1


def _request(operator, *, payload=None):
    return SimpleNamespace(
        json=payload or {},
        ctx=SimpleNamespace(
            session={
                "sub": operator.id,
                "tenant_id": "tenant-a",
                "account_info": operator,
            }
        ),
    )


@pytest.mark.asyncio
async def test_builtin_admin_rejects_every_other_operator_even_superadmin(monkeypatch):
    operator = SimpleNamespace(id="other-id", username="other", tenant_id="tenant-a")
    target = _TargetAccount()
    monkeypatch.setattr(user_views, "is_superadmin", lambda _account: True)

    with pytest.raises(CustomError):
        await user_views._ensure_can_manage_target_user(_request(operator), target)

    assert target.related_fetches == 0


@pytest.mark.asyncio
async def test_builtin_admin_can_manage_own_account(monkeypatch):
    operator = SimpleNamespace(id="admin-id", username="admin", tenant_id="tenant-a")
    target = _TargetAccount()
    monkeypatch.setattr(user_views, "is_superadmin", lambda _account: True)

    await user_views._ensure_can_manage_target_user(_request(operator), target)

    assert target.related_fetches == 1


@pytest.mark.asyncio
async def test_role_assignment_checks_admin_protection_before_mutation(monkeypatch):
    operator = SimpleNamespace(id="other-id", username="other", tenant_id="tenant-a")
    target = _TargetAccount()

    class FakeAccount:
        @staticmethod
        async def get_or_none(**_kwargs):
            return target

    async def reject(_request, _target):
        raise CustomError(user_views.ErrorCode.CANNOT_OPERATE_SUPERADMIN)

    monkeypatch.setattr(user_views, "Account", FakeAccount)
    monkeypatch.setattr(user_views, "_ensure_can_manage_target_user", reject)
    request = _request(
        operator,
        payload={
            "username": "admin",
            "add_role_names": "test",
            "remove_role_names": "系统管理员",
        },
    )

    with pytest.raises(CustomError):
        await inspect.unwrap(user_views.assign_role_to_user)(request)

    assert target.role_mutations == 0


@pytest.mark.asyncio
async def test_mfa_reset_checks_admin_protection_before_write(monkeypatch):
    operator = SimpleNamespace(id="other-id", username="other", tenant_id="tenant-a")
    target = _TargetAccount()

    class FakeAccount:
        @staticmethod
        async def get_or_none(**_kwargs):
            return target

    async def reject(_request, _target):
        raise CustomError(user_views.ErrorCode.CANNOT_OPERATE_SUPERADMIN)

    monkeypatch.setattr(user_views, "Account", FakeAccount)
    monkeypatch.setattr(user_views, "_ensure_can_manage_target_user", reject)
    request = _request(operator, payload={"username": "admin"})

    with pytest.raises(CustomError):
        await inspect.unwrap(user_views.refresh_mfa_key)(request)

    assert target.saved == 0
