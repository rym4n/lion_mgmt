import inspect
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from argon2.exceptions import VerificationError

from libs.error_code.exception import CustomError
from services.user import views as user_views


class _PasswordHasher:
    def __init__(self):
        self.verified = []

    def verify(self, password_hash, password):
        self.verified.append((password_hash, password))
        if password != "correct-current":
            raise VerificationError("mismatch")

    def hash(self, password):
        return f"hashed:{password}"


class _UpdateQuery:
    def __init__(self):
        self.updates = []

    async def update(self, **data):
        self.updates.append(data)


def _request(account, payload):
    return SimpleNamespace(
        json=payload,
        ctx=SimpleNamespace(
            session={
                "sub": account.id,
                "tenant_id": "tenant-a",
                "account_info": account,
            }
        ),
    )


@pytest.mark.asyncio
async def test_self_service_password_change_requires_current_password(monkeypatch):
    account = SimpleNamespace(
        id="self-id",
        password="stored-hash",
        last_login_time=datetime(2026, 7, 29, tzinfo=UTC),
    )
    query = _UpdateQuery()
    hasher = _PasswordHasher()

    class _Account:
        @staticmethod
        def filter(**kwargs):
            assert kwargs == {"id": "self-id"}
            return query

    async def validate_password(_password):
        return None

    monkeypatch.setattr(user_views, "Account", _Account)
    monkeypatch.setattr(user_views, "password_hasher", hasher)
    monkeypatch.setattr(user_views, "validate_password", validate_password)

    with pytest.raises(CustomError) as error:
        await inspect.unwrap(user_views.update_user)(
            _request(account, {"password": "new-password"})
        )

    assert error.value.enum is user_views.ErrorCode.CREDENTIALS_ERROR
    assert query.updates == []


@pytest.mark.asyncio
async def test_self_service_password_change_accepts_verified_current_password(
    monkeypatch,
):
    account = SimpleNamespace(
        id="self-id",
        password="stored-hash",
        last_login_time=datetime(2026, 7, 29, tzinfo=UTC),
    )
    query = _UpdateQuery()
    hasher = _PasswordHasher()

    class _Account:
        @staticmethod
        def filter(**_kwargs):
            return query

    async def validate_password(_password):
        return None

    monkeypatch.setattr(user_views, "Account", _Account)
    monkeypatch.setattr(user_views, "password_hasher", hasher)
    monkeypatch.setattr(user_views, "validate_password", validate_password)
    monkeypatch.setattr(user_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(user_views.update_user)(
        _request(
            account,
            {
                "current_password": "correct-current",
                "password": "new-password",
            },
        )
    )

    assert result == "success"
    assert hasher.verified == [("stored-hash", "correct-current")]
    assert query.updates == [{"password": "hashed:new-password"}]


@pytest.mark.asyncio
async def test_first_login_forced_password_change_keeps_authenticated_exemption(
    monkeypatch,
):
    account = SimpleNamespace(
        id="self-id",
        password="stored-hash",
        last_login_time=None,
    )
    query = _UpdateQuery()
    hasher = _PasswordHasher()

    class _Account:
        @staticmethod
        def filter(**_kwargs):
            return query

    async def validate_password(_password):
        return None

    monkeypatch.setattr(user_views, "Account", _Account)
    monkeypatch.setattr(user_views, "password_hasher", hasher)
    monkeypatch.setattr(user_views, "validate_password", validate_password)
    monkeypatch.setattr(user_views, "response_ok", lambda data: data)

    await inspect.unwrap(user_views.update_user)(
        _request(account, {"password": "new-password"})
    )

    assert hasher.verified == []
    assert query.updates[0]["password"] == "hashed:new-password"
    assert isinstance(query.updates[0]["last_login_time"], datetime)


class _ManagedAccount:
    def __init__(self, account_id):
        self.id = account_id
        self.username = "target"
        self.password = "stored-hash"
        self.last_login_time = datetime(2026, 7, 29, tzinfo=UTC)
        self.saved = 0

    async def save(self):
        self.saved += 1

    def to_json(self, _tenant_id):
        return {"id": self.id, "username": self.username}


class _AccountLookup:
    def __init__(self, account):
        self.account = account

    def prefetch_related(self, *_fields):
        return self

    def __await__(self):
        async def resolve():
            return self.account

        return resolve().__await__()


@pytest.mark.asyncio
async def test_admin_can_reset_another_users_password_without_their_old_password(
    monkeypatch,
):
    operator = SimpleNamespace(id="operator-id")
    target = _ManagedAccount("target-id")
    hasher = _PasswordHasher()

    class _Account:
        @staticmethod
        def get_or_none(**_kwargs):
            return _AccountLookup(target)

    async def allowed(*_args):
        return None

    async def validate_password(_password):
        return None

    monkeypatch.setattr(user_views, "Account", _Account)
    monkeypatch.setattr(user_views, "_ensure_can_manage_target_user", allowed)
    monkeypatch.setattr(user_views, "validate_password", validate_password)
    monkeypatch.setattr(user_views, "password_hasher", hasher)
    monkeypatch.setattr(user_views, "response_ok", lambda data: data)

    result = await inspect.unwrap(user_views.edit_user)(
        _request(operator, {"username": "target", "password": "new-password"})
    )

    assert result == {"id": "target-id", "username": "target"}
    assert hasher.verified == []
    assert target.password == "hashed:new-password"
    assert target.saved == 1


@pytest.mark.asyncio
async def test_admin_edit_route_still_verifies_when_target_is_self(monkeypatch):
    operator = SimpleNamespace(id="operator-id")
    target = _ManagedAccount("operator-id")
    hasher = _PasswordHasher()

    class _Account:
        @staticmethod
        def get_or_none(**_kwargs):
            return _AccountLookup(target)

    async def allowed(*_args):
        return None

    async def validate_password(_password):
        return None

    monkeypatch.setattr(user_views, "Account", _Account)
    monkeypatch.setattr(user_views, "_ensure_can_manage_target_user", allowed)
    monkeypatch.setattr(user_views, "validate_password", validate_password)
    monkeypatch.setattr(user_views, "password_hasher", hasher)

    with pytest.raises(CustomError) as error:
        await inspect.unwrap(user_views.edit_user)(
            _request(operator, {"username": "target", "password": "new-password"})
        )

    assert error.value.enum is user_views.ErrorCode.CREDENTIALS_ERROR
    assert target.saved == 0
