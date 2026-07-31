# coding=utf-8
import inspect
import time
from types import SimpleNamespace

import jwt
import pytest
from argon2.exceptions import VerificationError

from core.error_code import ErrorCode
from core.middlewares import security
from libs.error_code.exception import CustomError
from libs.security import authorization, mfa
from services.user import views as user_views
from settings import SECRET, SESSION_ENCODING_ALGORITHM


def _request(*, mfa_enabled=True, session_id="session-1", headers=None):
    account = SimpleNamespace(id="account-1", mfa_enable=mfa_enabled, roles=[])
    return SimpleNamespace(
        headers=headers or {},
        json={},
        ctx=SimpleNamespace(
            session={
                "sub": account.id,
                "id": session_id,
                "tenant_id": "tenant-1",
                "mfa_enable": mfa_enabled,
                "account_info": account,
            }
        ),
    )


@pytest.mark.asyncio
async def test_sensitive_proof_is_bound_to_current_session():
    request = _request()
    proof, expires_in = mfa.issue_sensitive_mfa_proof(request)
    request.headers[mfa.SENSITIVE_MFA_PROOF_HEADER] = proof

    await mfa.require_sensitive_mfa_proof(request)
    assert expires_in == 300

    request.ctx.session["id"] = "another-session"
    with pytest.raises(CustomError) as exc:
        await mfa.require_sensitive_mfa_proof(request)
    assert exc.value.enum is ErrorCode.SENSITIVE_MFA_REQUIRED


@pytest.mark.asyncio
async def test_expired_sensitive_proof_is_rejected():
    request = _request()
    now = int(time.time())
    proof = jwt.encode(
        {
            "purpose": mfa.SENSITIVE_MFA_PROOF_PURPOSE,
            "sub": "account-1",
            "sid": "session-1",
            "tenant_id": "tenant-1",
            "iat": now - 60,
            "exp": now - 1,
        },
        SECRET,
        SESSION_ENCODING_ALGORITHM,
    )
    request.headers[mfa.SENSITIVE_MFA_PROOF_HEADER] = proof

    with pytest.raises(CustomError) as exc:
        await mfa.require_sensitive_mfa_proof(request)
    assert exc.value.enum is ErrorCode.SENSITIVE_MFA_REQUIRED


@pytest.mark.asyncio
async def test_accounts_without_mfa_do_not_need_sensitive_proof():
    class AccountQuery:
        @staticmethod
        async def get_or_none(**_kwargs):
            return SimpleNamespace(mfa_enable=False)

    original_account = mfa.Account
    mfa.Account = AccountQuery
    try:
        await mfa.require_sensitive_mfa_proof(_request(mfa_enabled=False))
    finally:
        mfa.Account = original_account


@pytest.mark.asyncio
async def test_database_mfa_enable_takes_effect_without_relogin(monkeypatch):
    class AccountQuery:
        @staticmethod
        async def get_or_none(**_kwargs):
            return SimpleNamespace(mfa_enable=True)

    monkeypatch.setattr(mfa, "Account", AccountQuery)
    with pytest.raises(CustomError) as exc:
        await mfa.require_sensitive_mfa_proof(_request(mfa_enabled=False))
    assert exc.value.enum is ErrorCode.SENSITIVE_MFA_REQUIRED


@pytest.mark.asyncio
async def test_user_and_secure_permissions_require_step_up_after_rbac(monkeypatch):
    calls = []

    async def allow(_request, *_permissions):
        calls.append("rbac")

    async def verify(_request):
        calls.append("mfa")

    monkeypatch.setattr(authorization, "check_permissions", allow)
    monkeypatch.setattr(authorization, "require_sensitive_mfa_proof", verify)

    @authorization.requires_permission("user:r")
    async def protected(_request):
        calls.append("handler")

    await protected(_request())
    assert calls == ["rbac", "mfa", "handler"]


@pytest.mark.asyncio
async def test_unrelated_permission_does_not_require_step_up(monkeypatch):
    calls = []

    async def allow(_request, *_permissions):
        calls.append("rbac")

    async def forbidden(_request):
        raise AssertionError("unrelated module requested MFA")

    monkeypatch.setattr(authorization, "check_permissions", allow)
    monkeypatch.setattr(authorization, "require_sensitive_mfa_proof", forbidden)

    @authorization.requires_permission("workflow:r")
    async def protected(_request):
        calls.append("handler")

    await protected(_request())
    assert calls == ["rbac", "handler"]


class _PasswordAccount:
    def __init__(self, *, mfa_enable=True):
        self.id = "account-1"
        self.password = "old-hash"
        self.mfa_enable = mfa_enable
        self.last_login_time = None
        self.saved_fields = None

    async def save(self, *, update_fields):
        self.saved_fields = update_fields


class _AccountQuery:
    account = None

    @classmethod
    async def get_or_none(cls, **_kwargs):
        return cls.account


class _Cache:
    def __init__(self):
        self.saved = None

    async def set(self, key, value, ttl):
        self.saved = (key, value, ttl)


def _password_request(account, *, mfa_enabled=True):
    cache = _Cache()
    return SimpleNamespace(
        json={
            "old_password": "old-password",
            "new_password": "new-password",
            "mfa_code": "123456",
        },
        ctx=SimpleNamespace(
            session={
                "sub": account.id,
                "id": "session-1",
                "tenant_id": "tenant-1",
                "mfa_enable": mfa_enabled,
                "account_info": account,
            }
        ),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=cache)),
    )


@pytest.mark.asyncio
async def test_change_password_checks_old_password_and_mfa(monkeypatch):
    account = _PasswordAccount()
    _AccountQuery.account = account
    request = _password_request(account)
    calls = []

    monkeypatch.setattr(user_views, "Account", _AccountQuery)
    monkeypatch.setattr(
        user_views,
        "password_hasher",
        SimpleNamespace(
            verify=lambda hashed, plain: calls.append(("old", hashed, plain)),
            hash=lambda password: f"hash:{password}",
        ),
    )

    async def validate(password):
        calls.append(("policy", password))

    async def verify_mfa(_request, code):
        calls.append(("mfa", code))

    monkeypatch.setattr(user_views, "validate_password", validate)
    monkeypatch.setattr(user_views, "verify_session_mfa_code", verify_mfa)

    response = await inspect.unwrap(user_views.change_password)(request)

    assert response.status == 200
    assert calls == [
        ("old", "old-hash", "old-password"),
        ("policy", "new-password"),
        ("mfa", "123456"),
    ]
    assert account.password == "hash:new-password"
    assert account.saved_fields == ["password", "last_login_time"]
    assert request.app.ctx.cache_client.saved[0] == "account_account-1"


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_old_password_before_write(monkeypatch):
    account = _PasswordAccount()
    _AccountQuery.account = account
    request = _password_request(account)

    monkeypatch.setattr(user_views, "Account", _AccountQuery)

    def reject(_hashed, _plain):
        raise VerificationError("mismatch")

    monkeypatch.setattr(
        user_views,
        "password_hasher",
        SimpleNamespace(verify=reject),
    )

    with pytest.raises(CustomError) as exc:
        await inspect.unwrap(user_views.change_password)(request)
    assert exc.value.enum is ErrorCode.CREDENTIALS_ERROR
    assert account.saved_fields is None


@pytest.mark.asyncio
async def test_profile_update_ignores_legacy_password_field(monkeypatch):
    updates = []

    class FilteredAccount:
        async def update(self, **kwargs):
            updates.append(kwargs)

    class ProfileAccount:
        @staticmethod
        def filter(**_kwargs):
            return FilteredAccount()

    monkeypatch.setattr(user_views, "Account", ProfileAccount)
    request = _request()
    request.json = {"email": "user@example.com", "password": "bypass"}

    await inspect.unwrap(user_views.update_user)(request)
    assert updates == [{"email": "user@example.com"}]


@pytest.mark.parametrize(
    ("path", "payload", "secrets"),
    (
        (
            "/user/change-password",
            {
                "old_password": "old-secret",
                "new_password": "new-secret",
                "mfa_code": "123456",
            },
            ("old-secret", "new-secret", "123456"),
        ),
        ("/user/sensitive-mfa-auth", {"code": "654321"}, ("654321",)),
        ("/user/edit", {"password": "admin-reset"}, ("admin-reset",)),
    ),
)
def test_user_security_audit_redacts_passwords_and_mfa_codes(
    path, payload, secrets
):
    request = SimpleNamespace(
        path=path,
        json=payload,
        content_type="application/json",
        body=b"must-not-use-raw-body",
    )

    audit_data = security._redacted_user_request_data(request)

    assert "******" in audit_data
    assert "must-not-use-raw-body" not in audit_data
    for secret in secrets:
        assert secret not in audit_data
