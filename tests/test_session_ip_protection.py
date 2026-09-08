# coding=utf-8
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from libs.security import authentication


class _CacheClient:
    async def get(self, _key):
        if _key == authentication.SESSION_HIJACKING_PROTECTION_CACHE_KEY:
            return None
        return SimpleNamespace(id="account-1")

    async def set(self, _key, _value):
        return None


class _EmptyConfigQuery:
    async def all(self):
        return []


def _request(client_ip: str):
    return SimpleNamespace(
        path="/user/user-info",
        headers={},
        ip=client_ip,
        cookies={},
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=_CacheClient())),
        ctx=SimpleNamespace(),
    )


@pytest.fixture
def decoded_payload(monkeypatch):
    async def fake_token_decode(_request):
        return {"sub": "account-1", "exp": 4102444800, "ip": "192.0.2.10"}

    monkeypatch.setattr(
        authentication,
        "token_decode",
        fake_token_decode,
    )


def _set_protection(monkeypatch, enabled: bool):
    async def fake_get_secure_config_dict():
        return {"session_hijacking_protection": enabled}

    monkeypatch.setattr(
        authentication,
        "get_secure_config_dict",
        fake_get_secure_config_dict,
    )


@pytest.mark.asyncio
async def test_session_hijacking_protection_defaults_to_disabled(monkeypatch):
    monkeypatch.setattr(
        authentication.Config,
        "filter",
        lambda **_kwargs: _EmptyConfigQuery(),
    )

    config = await authentication.get_secure_config_dict()

    assert config["session_hijacking_protection"] is False


@pytest.mark.asyncio
async def test_ip_change_is_allowed_when_session_hijacking_protection_is_disabled(
    monkeypatch, decoded_payload
):
    _set_protection(monkeypatch, False)

    request = _request("192.0.2.20")
    await authentication.authenticate(request)

    assert request.ctx.session["ip"] == "192.0.2.10"


@pytest.mark.asyncio
async def test_ip_change_invalidates_session_when_session_hijacking_protection_is_enabled(
    monkeypatch, decoded_payload
):
    _set_protection(monkeypatch, True)

    with pytest.raises(CustomError) as exc_info:
        await authentication.authenticate(_request("192.0.2.20"))

    assert exc_info.value.enum == ErrorCode.SESSION_INVALID


@pytest.mark.asyncio
async def test_same_ip_remains_valid_when_session_hijacking_protection_is_enabled(
    monkeypatch, decoded_payload
):
    _set_protection(monkeypatch, True)

    request = _request("192.0.2.10")
    await authentication.authenticate(request)

    assert request.ctx.session["ip"] == "192.0.2.10"
