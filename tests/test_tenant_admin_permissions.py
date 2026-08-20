import ast
import asyncio
import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.error_code import ErrorCode
from libs.error_code.exception import CustomError
from services.tenant import views as tenant_views


ROOT = Path(__file__).resolve().parents[1]


def _function(path: Path, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text())
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def test_new_tenant_admin_uses_platform_wildcard_permissions():
    function = _function(ROOT / "services/tenant/views.py", "add_tenant")
    role_create = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "create"
        and any(keyword.arg == "permissions" for keyword in node.value.keywords)
    )
    permissions = next(
        keyword.value
        for keyword in role_create.value.keywords
        if keyword.arg == "permissions"
    )

    assert ast.literal_eval(permissions) == ["*:*"]


def test_playbook_fallback_tenant_admin_uses_platform_wildcard_permissions():
    function = _function(ROOT / "services/workflow/views.py", "create_workflow")
    role_create = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "create"
        and any(keyword.arg == "permissions" for keyword in node.value.keywords)
    )
    permissions = next(
        keyword.value
        for keyword in role_create.value.keywords
        if keyword.arg == "permissions"
    )

    assert ast.literal_eval(permissions) == ["*:*"]


def test_workflow_import_uses_refined_tool_upload_permission():
    function = _function(ROOT / "services/workflow/views.py", "import_workflow")
    decorator_names = [
        decorator.args[0].id
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "requires_permission"
        and decorator.args
        and isinstance(decorator.args[0], ast.Name)
    ]

    assert "TOOL_UPLOAD_PERMISSION" in decorator_names


def test_existing_tenant_admin_roles_are_promoted_to_wildcard_permissions():
    migration = importlib.import_module(
        "migrations.models.78_20260717173000_promote_tenant_admin_permissions"
    )
    upgrade_sql = asyncio.run(migration.upgrade(None))

    assert '"name" = \'租户管理员\'' in upgrade_sql
    assert '"permissions" = \'["*:*\"]\'::jsonb' in upgrade_sql
    assert '"deleted" = False' in upgrade_sql
    assert "? '*:*'" in upgrade_sql


def test_tenant_creator_cannot_be_removed_from_members():
    with pytest.raises(CustomError) as exc_info:
        tenant_views._validate_tenant_member_removal(
            creator_id="creator",
            ids_to_remove={"creator"},
            admin_member_ids={"creator", "other-admin"},
        )

    assert exc_info.value.enum == ErrorCode.TENANT_CREATOR_MUST_REMAIN_ADMIN


def test_tenant_member_removal_must_leave_an_admin():
    with pytest.raises(CustomError) as exc_info:
        tenant_views._validate_tenant_member_removal(
            creator_id="creator",
            ids_to_remove={"only-admin"},
            admin_member_ids={"only-admin"},
        )

    assert exc_info.value.enum == ErrorCode.TENANT_MUST_HAVE_ADMIN


def test_tenant_member_removal_allows_a_non_creator_when_admin_remains():
    tenant_views._validate_tenant_member_removal(
        creator_id="creator",
        ids_to_remove={"admin-1"},
        admin_member_ids={"admin-1", "admin-2"},
    )


def test_admin_member_query_orders_by_projected_id_before_distinct():
    source = (ROOT / "services/tenant/views.py").read_text()
    query = source.split("admin_member_ids = set(", 1)[1].split(
        "_validate_tenant_member_removal(", 1
    )[0]

    assert query.index('.order_by("id")') < query.index(".distinct()")
    assert query.index(".distinct()") < query.index('values_list("id", flat=True)')


def test_tenant_switch_uses_membership_instead_of_current_tenant_permission():
    function = _function(ROOT / "services/tenant/views.py", "switch_tenant")
    decorators = [
        decorator
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "requires_permission"
    ]
    source = ast.unparse(function)

    assert decorators == []
    assert "Tenant.get_or_none(id=tenant_id, deleted=False)" in source
    assert "tenant.member.filter(id=account.id, deleted=False).exists()" in source
    membership_guard = source.index("tenant.member.filter(")
    tenant_change = source.index("request.ctx.session['tenant_id'] = tenant.id")
    assert membership_guard < tenant_change
    assert "raise CustomError(ErrorCode.NOT_EXIST_IN_TENANT)" in source


class _MembershipQuery:
    def __init__(self, exists):
        self._exists = exists

    async def exists(self):
        return self._exists


class _TenantMembers:
    def __init__(self, exists):
        self._exists = exists

    def filter(self, **_filters):
        return _MembershipQuery(self._exists)


class _CookieJar:
    def __init__(self):
        self.calls = []

    def add_cookie(self, *args, **kwargs):
        self.calls.append((args, kwargs))


async def _run_tenant_switch(monkeypatch, *, is_member, session=None):
    target = SimpleNamespace(
        id=tenant_views.DEFAULT_TENANT_ID,
        member=_TenantMembers(is_member),
    )

    class TenantQuery:
        def prefetch_related(self, *_relations):
            return self

        def __await__(self):
            async def resolve():
                return target

            return resolve().__await__()

    class Cache:
        async def get(self, _key):
            return False

    response = SimpleNamespace(cookies=_CookieJar())
    monkeypatch.setattr(
        tenant_views.Tenant,
        "get_or_none",
        lambda **_filters: TenantQuery(),
    )
    monkeypatch.setattr(tenant_views, "refresh_token", lambda _session: "new-token")
    monkeypatch.setattr(tenant_views, "response_ok", lambda _data: response)
    if session is None:
        session = {
            "id": "session-1",
            "tenant_id": "tenant-current",
            "account_info": SimpleNamespace(id="account-1"),
        }
    request = SimpleNamespace(
        json={"id": target.id},
        ctx=SimpleNamespace(session=session),
        app=SimpleNamespace(ctx=SimpleNamespace(cache_client=Cache())),
    )

    result = await inspect.unwrap(tenant_views.switch_tenant)(request)
    return result, response, session


@pytest.mark.asyncio
async def test_tenant_member_can_switch_without_a_business_role(monkeypatch):
    result, response, session = await _run_tenant_switch(
        monkeypatch,
        is_member=True,
    )

    assert result is response
    assert session["tenant_id"] == tenant_views.DEFAULT_TENANT_ID
    assert response.cookies.calls[0][0][:2] == ("aioe-auth", "new-token")


@pytest.mark.asyncio
async def test_non_member_cannot_switch_tenant(monkeypatch):
    session = {
        "id": "session-1",
        "tenant_id": "tenant-current",
        "account_info": SimpleNamespace(id="account-1"),
    }
    with pytest.raises(CustomError) as exc_info:
        await _run_tenant_switch(
            monkeypatch,
            is_member=False,
            session=session,
        )

    assert exc_info.value.enum == ErrorCode.NOT_EXIST_IN_TENANT
    assert session["tenant_id"] == "tenant-current"


def test_admin_recovery_restores_creator_and_only_an_existing_admin_member():
    assert tenant_views._tenant_admin_recovery_target_ids(
        creator_id="creator",
        operator_id="admin",
        member_ids={"admin"},
    ) == {"creator", "admin"}
    assert tenant_views._tenant_admin_recovery_target_ids(
        creator_id="creator",
        operator_id="admin",
        member_ids=set(),
    ) == {"creator"}
    assert tenant_views._tenant_admin_recovery_target_ids(
        creator_id=None,
        operator_id="admin",
        member_ids=set(),
    ) == set()


def test_admin_recovery_requires_builtin_admin_in_default_tenant(monkeypatch):
    operator = SimpleNamespace(username="admin")
    monkeypatch.setattr(tenant_views, "is_superadmin", lambda account: account is operator)

    assert tenant_views._can_restore_tenant_admin(
        operator, tenant_views.DEFAULT_TENANT_ID, "other-tenant"
    )
    assert not tenant_views._can_restore_tenant_admin(
        operator, "other-tenant", "target-tenant"
    )
    assert not tenant_views._can_restore_tenant_admin(
        operator, tenant_views.DEFAULT_TENANT_ID, tenant_views.DEFAULT_TENANT_ID
    )
    assert not tenant_views._can_restore_tenant_admin(
        SimpleNamespace(username="other"),
        tenant_views.DEFAULT_TENANT_ID,
        "target-tenant",
    )


@pytest.mark.asyncio
async def test_knowledge_provision_failure_is_logged_without_failing_create(
    monkeypatch,
):
    warnings = []

    async def fail(_tenant_id):
        raise RuntimeError("OpenViking unavailable")

    monkeypatch.setattr(tenant_views, "provision_tenant_knowledge", fail)
    monkeypatch.setattr(
        tenant_views,
        "logger",
        SimpleNamespace(warning=lambda *args, **kwargs: warnings.append((args, kwargs))),
    )

    await tenant_views._provision_tenant_knowledge_best_effort("tenant-a")

    assert warnings
    assert warnings[0][0][-1] == "tenant-a"
    assert warnings[0][1]["exc_info"] is True
