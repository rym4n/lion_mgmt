# coding=utf-8
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types


class FakeRoles(list):
    async def add(self, role):
        self.append(role)


class FakeCredential(SimpleNamespace):
    async def save(self, update_fields=None):
        self.saved_update_fields = update_fields


class FakeAccount(SimpleNamespace):
    async def fetch_related(self, relation):
        self.fetch_related_calls.append(relation)


def _load_module(*, credential=None, account=None, role=None, cache=None):
    for name in [
        "aiocache",
        "libs",
        "libs.security",
        "libs.security.authentication",
        "libs.utils",
        "models",
        "models.digital_employee",
        "models.user",
        "sanic",
        "sanic.log",
        "settings",
        "tortoise",
        "tortoise.transactions",
    ]:
        sys.modules.pop(name, None)

    cache = cache or SimpleNamespace(set_calls=[], delete_calls=[])

    async def cache_set(key, value, ttl):
        cache.set_calls.append((key, value, ttl))

    async def cache_delete(key):
        cache.delete_calls.append(key)

    aiocache = types.ModuleType("aiocache")
    aiocache.caches = SimpleNamespace(get=lambda name: SimpleNamespace(set=cache_set, delete=cache_delete))
    sys.modules["aiocache"] = aiocache

    libs = types.ModuleType("libs")
    libs.__path__ = []
    sys.modules["libs"] = libs
    security = types.ModuleType("libs.security")
    security.__path__ = []
    sys.modules["libs.security"] = security
    authentication = types.ModuleType("libs.security.authentication")
    authentication.token_encode = lambda *args, **kwargs: "signed-token"
    sys.modules["libs.security.authentication"] = authentication
    utils = types.ModuleType("libs.utils")
    utils.password_hasher = SimpleNamespace(hash=lambda value: f"hashed-{value}")
    sys.modules["libs.utils"] = utils

    models = types.ModuleType("models")
    models.__path__ = []
    sys.modules["models"] = models
    digital_employee = types.ModuleType("models.digital_employee")

    class EmployeeApiCredential:
        @classmethod
        async def get_or_none(cls, **kwargs):
            return credential

        @classmethod
        async def create(cls, **kwargs):
            return FakeCredential(**kwargs)

    digital_employee.EmployeeApiCredential = EmployeeApiCredential
    sys.modules["models.digital_employee"] = digital_employee

    user = types.ModuleType("models.user")

    class Account:
        @classmethod
        async def get_or_none(cls, **kwargs):
            return account

        @classmethod
        async def create(cls, **kwargs):
            return FakeAccount(**kwargs, roles=FakeRoles(), fetch_related_calls=[])

    class Role:
        @classmethod
        async def get_or_none(cls, **kwargs):
            Role.last_query = kwargs
            return role

    user.Account = Account
    user.Role = Role
    sys.modules["models.user"] = user

    sanic = types.ModuleType("sanic")
    sanic_log = types.ModuleType("sanic.log")
    sanic_log.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
    sys.modules["sanic"] = sanic
    sys.modules["sanic.log"] = sanic_log

    settings = types.ModuleType("settings")
    settings.TZ = None
    sys.modules["settings"] = settings

    tortoise = types.ModuleType("tortoise")
    transactions = types.ModuleType("tortoise.transactions")
    transactions.in_transaction = lambda: None
    sys.modules["tortoise"] = tortoise
    sys.modules["tortoise.transactions"] = transactions

    path = Path(__file__).resolve().parents[2] / "services/digital_employee/aioe_credential_manager.py"
    spec = importlib.util.spec_from_file_location("aioe_credential_manager_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, cache, user.Role


def test_existing_employee_api_credential_syncs_current_rbac_role_and_reissues_token():
    credential = FakeCredential(
        employee_id="employee-1",
        account_id="account-1",
        api_token="old-token",
        saved_update_fields=None,
    )
    account = FakeAccount(
        id="account-1",
        username="digital-employee-employee-1",
        display_name="数字员工 API 账号",
        mfa_enable=False,
        tenant_id="tenant-1",
        password="password-hash",
        permit_ip=None,
        roles=FakeRoles(),
        fetch_related_calls=[],
    )
    role = SimpleNamespace(id="role-admin", tenant_id="tenant-1")
    manager, cache, role_model = _load_module(credential=credential, account=account, role=role)
    employee = SimpleNamespace(id="employee-1", tenant_id="tenant-1", rbac_role_id="role-admin")

    result = asyncio.run(manager.create_employee_api_account(employee))

    assert result is credential
    assert credential.api_token == "account-1:signed-token"
    assert credential.saved_update_fields == ["api_token", "utime"]
    assert account.roles == [role]
    assert role_model.last_query == {"id": "role-admin", "tenant_id": "tenant-1", "deleted": False}
    assert cache.set_calls and cache.set_calls[0][0] == "account_account-1"


def test_get_employee_api_token_uses_sync_path_for_existing_credential():
    credential = FakeCredential(
        employee_id="employee-1",
        account_id="account-1",
        api_token="old-token",
        saved_update_fields=None,
    )
    account = FakeAccount(
        id="account-1",
        username="digital-employee-employee-1",
        display_name="数字员工 API 账号",
        mfa_enable=False,
        tenant_id="tenant-1",
        password="password-hash",
        permit_ip=None,
        roles=FakeRoles(),
        fetch_related_calls=[],
    )
    role = SimpleNamespace(id="role-admin", tenant_id="tenant-1")
    manager, _cache, _role_model = _load_module(credential=credential, account=account, role=role)
    employee = SimpleNamespace(id="employee-1", tenant_id="tenant-1", rbac_role_id="role-admin")

    token = asyncio.run(manager.get_employee_api_token(employee))

    assert token == "account-1:signed-token"
    assert account.roles == [role]
