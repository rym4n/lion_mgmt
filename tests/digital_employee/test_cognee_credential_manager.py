# coding=utf-8
import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import types


def _load_module(name: str, relative_path: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CustomError(Exception):
    def __init__(self, enum):
        self.enum = enum


class ErrorCode:
    COGNEE_AUTH_FAILED = "COGNEE_AUTH_FAILED"


class FakeCredential:
    objects = {}
    created = []
    saved = []

    def __init__(
        self,
        tenant_id,
        cognee_user_id,
        cognee_user_email,
        cognee_user_password_enc,
        cognee_jwt=None,
        cognee_jwt_expires_at=None,
        deleted=False,
    ):
        self.tenant_id = tenant_id
        self.cognee_user_id = cognee_user_id
        self.cognee_user_email = cognee_user_email
        self.cognee_user_password_enc = cognee_user_password_enc
        self.cognee_jwt = cognee_jwt
        self.cognee_jwt_expires_at = cognee_jwt_expires_at
        self.deleted = deleted

    @classmethod
    async def get_or_none(cls, tenant_id, deleted=False):
        item = cls.objects.get(tenant_id)
        if item and item.deleted == deleted:
            return item
        return None

    @classmethod
    async def create(cls, **kwargs):
        item = cls(**kwargs)
        cls.objects[item.tenant_id] = item
        cls.created.append(kwargs)
        return item

    async def save(self, update_fields=None):
        self.saved.append((self.tenant_id, update_fields))


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAsyncClient:
    calls = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        if url.endswith("/api/v1/auth/jwt/login") and json["email"] == "admin@aioe.internal":
            return FakeResponse(200, {"access_token": "admin-token"})
        if url.endswith("/api/v1/auth/register"):
            assert headers == {"Authorization": "Bearer admin-token"}
            return FakeResponse(201, {"id": "remote-user-1"})
        if url.endswith("/api/v1/auth/jwt/login") and json["email"] == "tenant-1@aioe.internal":
            return FakeResponse(200, {"access_token": "tenant-token"})
        return FakeResponse(404, {})


def _install_stubs():
    FakeCredential.objects = {}
    FakeCredential.created = []
    FakeCredential.saved = []
    FakeAsyncClient.calls = []

    sys.modules.setdefault("core", types.ModuleType("core"))
    core_error_code = types.ModuleType("core.error_code")
    core_error_code.ErrorCode = ErrorCode
    sys.modules["core.error_code"] = core_error_code

    sys.modules.setdefault("libs", types.ModuleType("libs"))
    sys.modules.setdefault("libs.error_code", types.ModuleType("libs.error_code"))
    exception_module = types.ModuleType("libs.error_code.exception")
    exception_module.CustomError = CustomError
    sys.modules["libs.error_code.exception"] = exception_module

    models_module = types.ModuleType("models")
    models_module.__path__ = []
    sys.modules["models"] = models_module
    digital_employee_module = types.ModuleType("models.digital_employee")
    digital_employee_module.TenantCogneeCredential = FakeCredential
    sys.modules["models.digital_employee"] = digital_employee_module

    settings_module = types.ModuleType("settings")
    settings_module.LION_MEMORY_ADMIN_EMAIL = "admin@aioe.internal"
    settings_module.LION_MEMORY_ADMIN_PASSWORD = "admin-pass"
    settings_module.LION_MEMORY_ENDPOINT = "http://lion-memory"
    settings_module.TZ = timezone(timedelta(hours=8))
    sys.modules["settings"] = settings_module

    sanic_module = types.ModuleType("sanic")
    sanic_log_module = types.ModuleType("sanic.log")
    sanic_log_module.logger = SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)
    sys.modules["sanic"] = sanic_module
    sys.modules["sanic.log"] = sanic_log_module

    httpx_module = types.ModuleType("httpx")
    httpx_module.AsyncClient = FakeAsyncClient
    httpx_module.HTTPError = Exception
    httpx_module.HTTPStatusError = RuntimeError
    sys.modules["httpx"] = httpx_module


def test_get_or_refresh_jwt_registers_remote_user_and_returns_tenant_token():
    _install_stubs()
    module = _load_module(
        "services.digital_employee.cognee_credential_manager",
        "services/digital_employee/cognee_credential_manager.py",
    )

    token = asyncio.run(module.get_or_refresh_jwt("tenant-1"))
    credential = FakeCredential.objects["tenant-1"]

    assert token == "tenant-token"
    assert credential.cognee_user_id == "remote-user-1"
    assert credential.cognee_jwt == "tenant-token"
    assert credential.cognee_jwt_expires_at > datetime.now(timezone(timedelta(hours=8)))
    assert [call[0] for call in FakeAsyncClient.calls] == [
        "http://lion-memory/api/v1/auth/jwt/login",
        "http://lion-memory/api/v1/auth/register",
        "http://lion-memory/api/v1/auth/jwt/login",
    ]
