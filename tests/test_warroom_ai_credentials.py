# coding=utf-8
import asyncio
from types import SimpleNamespace

import jwt
import pytest

from libs.security import authentication
from services.warroom import aioe_credentials
from settings import SECRET


def test_build_warroom_aioe_credentials_delegates_current_account(monkeypatch):
    captured = {}

    def fake_token_encode(**kwargs):
        captured.update(kwargs)
        return "signed-token"

    monkeypatch.setattr(aioe_credentials, "token_encode", fake_token_encode)
    monkeypatch.setattr(aioe_credentials, "AIOE_INTERNAL_URL", "http://lion-mgmt:8000")
    account = SimpleNamespace(
        id="account-1",
        username="admin",
        display_name="Administrator",
        password="password-hash",
        mfa_enable=True,
        deleted=False,
        disabled=False,
        permit_ip="192.0.2.10",
    )

    result = aioe_credentials.build_warroom_aioe_credentials(account, "tenant-1")

    assert result == {
        "base_url": "http://lion-mgmt:8000",
        "token": "account-1:signed-token",
        "tenant_id": "tenant-1",
    }
    assert captured == {
        "account_id": "account-1",
        "username": "admin",
        "display_name": "Administrator",
        "mfa_enable": True,
        "tenant_id": "tenant-1",
        "token_id": captured["token_id"],
        "expiration": aioe_credentials.WARROOM_API_TOKEN_EXPIRATION_SECONDS,
        "mfa_auth": True,
    }


def test_build_warroom_aioe_credentials_signs_short_lived_user_scope(monkeypatch):
    monkeypatch.setattr(aioe_credentials, "AIOE_INTERNAL_URL", "http://lion-mgmt:8000")
    account = SimpleNamespace(
        id="account-1",
        username="admin",
        display_name="Administrator",
        password="password-hash",
        mfa_enable=True,
        deleted=False,
        disabled=False,
    )

    credential = aioe_credentials.build_warroom_aioe_credentials(account, "tenant-1")
    account_id, token = credential["token"].split(":", 1)
    payload = jwt.decode(token, SECRET, algorithms=["HS256"])

    assert account_id == account.id
    assert payload["sub"] == account.id
    assert payload["tenant_id"] == "tenant-1"
    assert payload["mfa_auth"] is True
    assert payload["exp"] - payload["iat"] == 5 * 60


def test_platform_auth_decodes_request_scoped_delegation_token():
    account = SimpleNamespace(
        id="account-1",
        username="oauth-user",
        display_name="OAuth User",
        password="",
        mfa_enable=False,
        deleted=False,
        disabled=False,
    )
    credential = aioe_credentials.build_warroom_aioe_credentials(account, "tenant-1")[
        "token"
    ]

    class _Cache:
        async def get(self, key):
            assert key == "account_account-1"
            return account

    request = SimpleNamespace(
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_Cache())),
        headers={},
    )

    payload = asyncio.run(authentication._decode_account_token(request, credential))

    assert payload["sub"] == account.id
    assert payload["tenant_id"] == "tenant-1"


def test_platform_auth_preserves_account_signed_api_tokens():
    account = SimpleNamespace(id="api-account-1", password="api-account-secret")
    token = authentication.token_encode(
        account_id=account.id,
        username="api-user",
        display_name="API User",
        mfa_enable=False,
        tenant_id="tenant-1",
        token_id="token-1",
        secret=account.password,
    )

    class _Cache:
        async def get(self, _key):
            return account

    request = SimpleNamespace(
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_Cache())),
        headers={},
    )

    payload = asyncio.run(
        authentication._decode_account_token(request, f"{account.id}:{token}")
    )

    assert payload["sub"] == account.id
    assert payload["tenant_id"] == "tenant-1"


def test_platform_auth_rejects_delegation_subject_prefix_mismatch():
    account = SimpleNamespace(id="account-2", password="")
    source = SimpleNamespace(
        id="account-1",
        username="oauth-user",
        display_name="OAuth User",
        password="",
        mfa_enable=False,
        deleted=False,
        disabled=False,
    )
    _, token = aioe_credentials.build_warroom_aioe_credentials(source, "tenant-1")[
        "token"
    ].split(":", 1)

    class _Cache:
        async def get(self, _key):
            return account

    request = SimpleNamespace(
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_Cache())),
        headers={},
    )

    with pytest.raises(jwt.InvalidTokenError, match="token subject"):
        asyncio.run(
            authentication._decode_account_token(request, f"{account.id}:{token}")
        )


@pytest.mark.parametrize("field", ["deleted", "disabled"])
def test_build_warroom_aioe_credentials_rejects_inactive_account(field):
    account = SimpleNamespace(
        id="account-1",
        username="admin",
        display_name="Administrator",
        password="password-hash",
        mfa_enable=False,
        deleted=False,
        disabled=False,
    )
    setattr(account, field, True)

    with pytest.raises(ValueError, match="active account"):
        aioe_credentials.build_warroom_aioe_credentials(account, "tenant-1")
